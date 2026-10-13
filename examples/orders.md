Customers sign in to our Auth0 identity provider over HTTPS with TLS 1.3, using
a password and MFA. Anyone can load our React single-page web app without
signing in; CloudFront serves it to the public internet over HTTPS with TLS 1.3.
The web app calls an internal Orders API in our AWS VPC over HTTPS. Every call
carries an Auth0 OAuth2 bearer token; the API rejects unauthenticated requests
and allows 100 requests per minute per customer. AWS WAF on CloudFront limits
each client to 2,000 requests per five minutes.

The Orders API reads and writes a Postgres database in a private subnet over
TLS, with IAM database authentication. It holds customer names, delivery
addresses, phone numbers, and order history, is encrypted at rest with AWS KMS,
and is reachable only from inside the VPC. Payment authorisation goes to Stripe
over HTTPS with a secret API key; card numbers never reach our systems.

A nightly batch job in the VPC reads at most one day of orders over TLS with a
read-only database role. It writes summaries over HTTPS, with its own IAM role,
to a KMS-encrypted S3 bucket. The analytics team, a group of internal analysts,
query that bucket through Athena, which reads it over TLS, with read- only IAM
roles.

Support administrators are staff who use an admin console on the Orders API from
a corporate management network over HTTPS, signing in with SSO and MFA. Through
it they refund orders, edit delivery addresses, and look up order history. The
API writes each action over HTTPS, with its IAM role, to a KMS- encrypted
CloudWatch Logs audit log that administrators cannot change. Only the platform
team can copy or restore the database snapshots, the bucket and the audit log,
through IAM roles with SSO and MFA over HTTPS.
