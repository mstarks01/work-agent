Invoicing service for small businesses.

We sell an invoicing service. Each customer company is one tenant, and all
companies share one billing app, one database and one PDF bucket in our cloud
platform.

People at a customer company use the billing app in their browser over HTTPS.
Each user belongs to one company and is either an admin or a viewer. They sign
in with an email address and a password and get a session cookie. There is no
second factor, for admins or viewers.

The billing app keeps every company's invoices, customers and payout bank
details in one PostgreSQL database, the tenant database. Every table has a
company ID column. The billing app adds the company filter to each query
itself, and the database has no row-level security. The app connects with one
database account for all companies.

When a user opens an invoice, the billing app loads it by its invoice number.
Nobody wrote down whether it also checks that the invoice belongs to the user's
company. Invoice numbers count up from 1000 across all companies.

The pages hide the buttons a viewer may not use. Nobody wrote down whether the
billing app checks the user's role again when a request arrives. The invoice
edit form posts the whole invoice back, and the billing app saves every field
it receives, including the paid flag and the company's payout bank account.

Each invoice email sends the payer a link that ends in the invoice number. The
payment page shows the invoice, the company's name and its bank details, and it
asks the payer to sign in to nothing.

To make a PDF, the billing app puts a render job on the render queue. The job
carries a company ID and an invoice number. The PDF renderer takes jobs off the
queue one at a time, reads the invoice from the tenant database and writes the
PDF to the PDF bucket under the company ID and the invoice number. It trusts the
company ID the job carries. Any user can ask for a PDF of any invoice, as often
as they like. The billing app fetches PDFs from the bucket with one service
credential for all companies.

Our support agents work from the support office network. The admin console is
reachable only from there, and each agent signs in to it with their own account
and a one-time code. From the admin console an agent can open a session in the
billing app as any customer user. Everything the agent then does is recorded in
the tenant database against that customer user, and not against the agent.

Nobody recorded how the billing app and the renderer reach the database, the
queue or the bucket, or how the database and the bucket are protected at rest.
