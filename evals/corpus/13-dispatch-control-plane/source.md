Dispatch control console.

We run our own field dispatch platform. Every part of it below is ours, apart
from the scheduling partner at the end.

Duty engineers sit on the corporate network. They open the dispatch console in
their browser. The console is a single-page JavaScript app and our corporate web
host serves it. Nobody wrote down how an engineer signs in to it.

The console calls the dispatch API. The dispatch API runs in the production
control plane. The control plane is the same company and the same staff, but it
is the zone that holds the live estate: an account there can move a crew, and it
can change the work a depot does that day. A corporate laptop holds none of
those rights. The control plane is not reachable from the internet.

The console and the API are served from different origins, so the API answers
the console cross-origin. Whoever configured CORS did not record which origins
the API allows, or whether it allows credentials.

The console also opens a WebSocket to the dispatch API and holds it open for
live job status. Nobody wrote down whether that socket runs over TLS, whether
its handshake is authenticated, or whether it checks the engineer's session
again after it is open.

The dispatch API reads and writes the dispatch database. That database holds the
job orders, the crew names and the crew mobile numbers. Every job order in it
carries the token or the session that created it, and nothing about the person
behind that token. How the database is protected at rest is not written down
anywhere.

A scheduling partner, which is another company, publishes tomorrow's planned
work. Our schedule importer runs on the corporate network. Every hour it pulls
the partner's SOAP feed over HTTPS and parses the XML document it gets back. It
writes each document it downloads into the schedule archive, a folder on the
corporate file store. Nothing was written down about how the partner identifies
our importer, or how our importer identifies the partner.

The importer then posts the parsed work orders to the same dispatch API the
console calls. It presents an API token. That token was issued when the importer
was built and nobody has rotated it since.
