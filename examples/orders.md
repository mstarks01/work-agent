Customers sign in with our Auth0 identity provider and place orders through a
React single-page web app, served from CloudFront to the public internet over
HTTPS with TLS 1.3. The web app calls an internal Orders API in our AWS VPC over
HTTPS. Every call carries an Auth0 OAuth2 bearer token, and the API rejects
unauthenticated requests and allows 100 requests per minute per customer. AWS
WAF on CloudFront limits each client to 2,000 requests per five minutes.

The Orders API reads and writes a Postgres database in a private subnet over
TLS, with IAM database authentication. It holds customer names, delivery
addresses, phone numbers, and order history, is encrypted at rest with AWS KMS,
and is reachable only from inside the VPC. Only the platform team can copy or
restore its snapshots. Payment authorisation goes to Stripe over HTTPS with a
secret API key; card numbers never reach our systems, and the API stores only
Stripe's charge identifier.

A nightly batch job in the same VPC reads at most one day of orders over TLS
with a read-only database role. It writes summaries over HTTPS, with its own IAM
role, to a KMS-encrypted S3 bucket; only the platform team can copy or restore
its objects. The analytics team, a group of internal analysts, query that bucket
through Athena over HTTPS the following morning with read-only IAM roles.

Support administrators are staff who use an admin console on the Orders API from
a separate corporate management network over HTTPS, signing in with SSO and MFA.
Through it they refund orders, edit customer delivery addresses, and look up
order history on a customer's behalf. The API writes each action to an audit log
that administrators cannot change.
