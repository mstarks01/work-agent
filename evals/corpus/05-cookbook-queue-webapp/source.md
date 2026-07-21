Web application with a queue-decoupled background process.

A user's browser talks to the web application over HTTP/S across the public
internet. The browser sits outside our network; the web application runs in our
web tier.

The web application does not do the heavy work itself. It puts jobs onto a
message queue, and a background worker process picks them up and does the work.
The queue, the worker and the database are all in the backend tier, behind the
web tier.

The background worker reads and writes the database. The database is also where
we keep the application's log records.

Both processes read their settings from a config store: the web application has
a web application config, and the worker has a worker config. Both of those
config stores hold the credentials the process needs — the web application's
queue credentials, and the worker's database credentials.

The diagram does not say anything about how the web application authenticates
to the queue, how the worker authenticates to the database, or whether anything
is encrypted at rest. The browser-to-application traffic is the one link marked
as encrypted.
