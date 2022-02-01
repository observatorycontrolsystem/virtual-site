virtual-site
####

Example application showcasing OCS API interactions needed to fulfill
observation requests at a telescope site.

You might find this helpful if you're looking to integrate your telescope's
control system with the Observatory Control System. Also, see
https://observatorycontrolsystem.github.io/integration/tcs/.

Usage
----

You can spin up the client straight from docker::

  $ docker run ghcr.io/observatorycontrolsystem/virtual-site --help
  $ docker run ghcr.io/observatorycontrolsystem/virtual-site \
      --name ogg \
      --api-url http://localhost:8000/api/ \
      --api-token 'sutoken1234abcd'

Development
----

Install dependencies::

  $ poetry install

Run::

  $ poetry run virtual-site --name ogg --api-url http://localhost:8000/api/ --api-token 'sutoken1234abcd'
