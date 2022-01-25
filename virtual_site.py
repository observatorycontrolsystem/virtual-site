import logging
import asyncio

import click
import httpx
import arrow


log = logging.getLogger(__name__)


@click.command()
@click.option("--name", help="name of this site (e.g. 'ogg')", required=True)
@click.option(
    "--api-url",
    help="OCS Portal API root URL (e.g. 'http://localhost:8000/api/'",
    required=True,
)
@click.option("--api-token", help="OCS Portal API auth token", required=True)
@click.option(
    "--log-level",
    help="log level",
    type=click.Choice(["critical", "error", "warning", "info", "debug"]),
    default="info",
)
def cli(name, api_url, api_token, log_level):
    logging.basicConfig(level=log_level.upper())

    s = Site(name, api_url, api_token)
    asyncio.run(s.run())


class Site:

  name: str

  api_client: httpx.AsyncClient

  last_sync_time: arrow.Arrow

  observation_tasks: list["ObservationTask"]


  def __init__(self, name: str, api_url: str, api_token: str):
      self.name = name
      self.api_client = httpx.AsyncClient(
          base_url=api_url,
          headers={
              "Authorization": f"Token {api_token}"
          },
          follow_redirects=True
      )

      self.last_sync_time = arrow.get(0)
      self.observation_tasks = []

  async def run(self) -> None:
      try:
          await self.poll_schedule()
      except asyncio.CancelledError:
          await self.api_client.aclose()

  async def poll_schedule(self) -> None:
      while True:
          log.info("checking for new schedule")

          r = await self.api_client.get("/last_scheduled/")
          last_scheduled_time = arrow.get(r.json()["last_schedule_time"])

          if self.last_sync_time <= last_scheduled_time:
              log.info("found new schedule")

              await self.sync_schedule()

          await asyncio.sleep(5)

  async def sync_schedule(self) -> None:
      log.info("synchronizing schedule")

      schedule = (await self.api_client.get(
          f"/schedule/",
          params={
              "site": self.name,
              "start_after": arrow.utcnow().isoformat(),
              "state": "PENDING",
          }
      )).json()

      self.last_sync_time = arrow.utcnow()

      log.info("cancelling previously scheduled tasks")
      for ot in self.observation_tasks:
          ot.cancel()

      # schedule the new ones
      log.info("scheduling new tasks")
      observation_tasks = []

      for obs in schedule["results"]:
          ot = ObservationTask(spec=obs, api_client=self.api_client)
          observation_tasks.append(ot)

      self.observation_tasks = observation_tasks

      log.info("schedule synchronized")


class ObservationTask:

    def __init__(self, spec: dict, api_client: httpx.AsyncClient):
        self.spec = spec
        self.api_client = api_client
        self.waiting = True

        self.log = logging.getLogger(repr(self))
        self.task = asyncio.create_task(self.run())

    async def run(self):
        start_time = arrow.get(self.spec["start"])
        end_time = arrow.get(self.spec["end"])

        self.log.info(
            f"waiting until observation start time: "
            f"{start_time} ({start_time.humanize()})"
        )
        await asyncio.sleep((start_time - arrow.utcnow()).total_seconds())
        self.waiting = False
        self.log.info("observation started")

        for config in self.spec["request"]["configurations"]:
            config_id = config["id"]
            status_id = config["configuration_status"]
            run_time = sum(
                ic["exposure_time"] * ic["exposure_count"]
                for ic in config["instrument_configs"]
            )

            self.log.info(
                f"notify OCS that we're about to start config {config_id}"
            )
            r = await self.api_client.patch(
                f"/configurationstatus/{status_id}/",
                json={"state": "ATTEMPTED"}
            )
            self.log.info(f"OCS response: {r.content}")
            if not r.is_success:
                return

            self.log.info(f"beep bop taking some pictures, wait {run_time} sec")
            config_start = arrow.utcnow()
            await asyncio.sleep(run_time)
            config_end = arrow.utcnow()

            time_completed = (config_end - config_start).total_seconds()

            self.log.info(
                f"notify OCS that we're done with config {config_id}"
            )
            r = await self.api_client.patch(
                f"/configurationstatus/{status_id}/",
                json={
                    "state": "COMPLETED",
                    "summary": {
                        "state": "COMPLETED",
                        "start": str(config_start),
                        "end": str(config_end),
                        "time_completed": time_completed,
                        "reason": "",
                        "events": {
                            "msg": "completed by virtual-site",
                            "events": ["payload", "can", "be whatever you like"]
                        }
                    }
                }
            )
            self.log.info(f"OCS response: {r.content}")
            if not r.is_success:
                return

        self.log.info(
            f"waiting until observation end time: "
            f"{end_time} ({end_time.humanize()})"
        )
        await asyncio.sleep((end_time - arrow.utcnow()).total_seconds())
        self.log.info("observation finished")

    def cancel(self) -> None:
        if not self.waiting:
            return

        self.log.info("cancelling")
        self.task.cancel()

    def __repr__(self):
        fqn = f"{self.__module__}.{self.__class__.__qualname__}"
        return f"<{fqn} id={self.spec['id']!r} name={self.spec['name']!r}>"
