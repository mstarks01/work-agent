Checkout and order-capture path for our storefront.

Shoppers browse the storefront and place orders through the storefront API, a
Node service running on Cloud Run. It is the only thing we expose to the
internet. Shoppers sign in with email and password and get a session cookie;
we have not rolled out MFA for shopper accounts yet.

When a shopper submits an order the storefront API calls the order service
over gRPC. The order service is a Python worker in our core network; it is
not exposed outside. There is no auth on that gRPC call — the order service
accepts anything that can reach it, which is meant to be only the storefront
API. Orders, shopper addresses and card-last-four live in a PostgreSQL
database (orders-db) that the order service reads and writes with a single
application account that has full read/write. The password comes from an
environment variable on the worker.

Every completed order also gets a receipt written to a Cloud Storage bucket
we call the receipt archive. That is on TLS with the order service's own
service account, and the bucket is encrypted with a customer-managed key. The
receipt records which order was written and that the order service wrote it.

Our card processor is a third party. After they settle a payment they POST a
webhook back to the storefront API to tell us the order is paid. I would have
to check how that callback is authenticated.
