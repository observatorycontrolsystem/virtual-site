import logging
import asyncio

import click
import httpx
import arrow


log = logging.getLogger(__name__)


@click.command()
@click.option(
    "--name",
    help="name of this site (e.g. 'ogg')",
    required=True,
    default="ogg"
)
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
    """
    A virtual controller that fulfills observation requests for a site.
    """
    # Just setting up logging.
    logging.basicConfig(level=log_level.upper())

    s = Site(name, api_url, api_token)

    # We're using asyncio just to make concurrent programming a bit
    # more legible. You can ignore the async/await syntax for the most part.
    asyncio.run(s.run())


class Site:

  name: str

  # Arrow is just like a datetime object but w/ full ISO 8601
  last_sync_time: arrow.Arrow

  observation_tasks: list["ObservationTask"]

  api_client: httpx.AsyncClient


  def __init__(self, name: str, api_url: str, api_token: str):
      self.name = name

      # setup a HTTP client we'll use to talk with API
      self.api_client = httpx.AsyncClient(
          base_url=api_url,
          headers={
              "Authorization": f"Token {api_token}"
          },
          follow_redirects=True
      )

      # this datetime is used to keep track of when to poll for a new schedule
      # set to "0" (epoch start) initially to always sync the schedule on
      # start-up
      self.last_sync_time = arrow.get(0)

      # a list of running or scheduled observation tasks
      self.observation_tasks = []

  async def run(self) -> None:
      """
      Run site tasks.
      """
      try:
          # we start polling the schedule in a coroutine
          await self.poll_schedule()
      except asyncio.CancelledError: # raised on CTRL-C
          # gracefully shut down the HTTP client
          await self.api_client.aclose()

  async def poll_schedule(self) -> None:
      """
      Poll the schedule every 5 seconds and schedule observations locally
      whenever a new schedule is detected.
      """
      while True:
          log.info("checking for new schedule")

          r = await self.api_client.get("/last_scheduled/")
          last_scheduled_time = arrow.get(r.json()["last_schedule_time"])

          if self.last_sync_time <= last_scheduled_time:
              log.info("found new schedule")

              await self.sync_schedule()

              self.last_sync_time = last_scheduled_time

          await asyncio.sleep(5)

  async def sync_schedule(self) -> None:
      """
      Retrieve the schedule from the API and create a ObservationTask for each
      observation.
      """
      log.info("synchronizing schedule")

      now = arrow.utcnow()
      schedule = (await self.api_client.get(
          f"/schedule/",
          params={
              "site": self.name,
              "start_after": now.isoformat(),
              "start_before": now.shift(days=7).isoformat(),
              "state": "PENDING",
          }
      )).json()

      log.info("cancelling previously scheduled tasks")
      for ot in self.observation_tasks:
          ot.cancel()

      log.info("scheduling new tasks")
      observation_tasks = []

      for obs in schedule["results"]:
          ot = ObservationTask(spec=obs, api_client=self.api_client)
          observation_tasks.append(ot)

      self.observation_tasks = observation_tasks

      log.info("schedule synchronized")


class ObservationTask:
    """
    A ObservationTask carries out the observation on the telescope/instruments
    (although in this case we just sleep).

    On instantiation, it creates a asyncio.Task to run in the background.
    """

    def __init__(self, spec: dict, api_client: httpx.AsyncClient):
        self.spec = spec
        self.api_client = api_client
        self.started = False

        self.log = logging.getLogger(repr(self))
        self.task = asyncio.create_task(self.run())

    async def run(self):
        """
        Perform an "observation".

        This task sits idle until the observation start time and then mocks
        the observation.
        """
        start_time = arrow.get(self.spec["start"])

        self.log.info(
            f"waiting until observation start time: "
            f"{start_time} ({start_time.humanize()})"
        )
        await asyncio.sleep((start_time - arrow.utcnow()).total_seconds())
        self.started = True
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

        self.log.info("observation finished")

    def cancel(self) -> None:
        """
        Cancel this observation if it hasn't started yet.
        """
        if not self.started:
            self.log.info(
                "skip cancelling because observation has already started"
            )
            return

        self.log.info("cancelling")
        self.task.cancel()

    def __repr__(self):
        fqn = f"{self.__module__}.{self.__class__.__qualname__}"
        return f"<{fqn} id={self.spec['id']!r} name={self.spec['name']!r}>"
