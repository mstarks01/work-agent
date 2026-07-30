Customers place orders through a React single-page web app, served to the public
internet over HTTPS with TLS 1.3. The web app calls an internal Orders API inside
our AWS VPC. Every call to that API carries an OAuth2 bearer token issued by our
identity provider, and the API rejects unauthenticated requests.

The Orders API reads and writes a Postgres database in a private subnet, holding
customer names, delivery addresses, phone numbers, and order history. The
database is encrypted at rest and reachable only from inside the VPC. Payment
authorisation is delegated to Stripe over TLS; card numbers never reach our
systems, and the Orders API stores only the charge identifier Stripe returns.

A nightly batch job running in the same VPC exports order summaries to an S3
bucket, which the analytics team queries the following morning.

Support administrators reach an admin console on the Orders API from a separate
corporate management network. Through it they refund orders, edit customer
delivery addresses, and look up order history on a customer's behalf.
