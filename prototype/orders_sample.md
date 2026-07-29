# Orders Service

PROTOTYPE SCRATCH SAMPLE — stand-in for the `examples/orders.md` settled by ticket #30.
Do not treat this file as the real sample; it exists so **Load example** has something to
load while the app's shape is being judged.

Customers use a React web app over HTTPS to place orders. The web app calls an internal
Orders API (OAuth2 bearer tokens) inside our VPC, which reads and writes an encrypted
Postgres database holding customer PII and payment details. A nightly job in the same VPC
exports order summaries to an S3 bucket. Admins reach the Orders API from a separate
management network.

The Orders API also publishes an `order.placed` event to an SQS queue; a fulfilment worker
in the VPC consumes it and calls a third-party shipping provider over the internet. The
shipping provider calls back into a webhook endpoint on the Orders API to report delivery
status; that endpoint validates an HMAC signature using a secret held in Secrets Manager.

Payment authorisation goes out to Stripe from the Orders API. Card numbers are never
stored — only the Stripe customer id and the last four digits.
