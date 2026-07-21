Supplier document portal.

Suppliers upload their compliance paperwork through a portal — insurance
certificates, food safety audits, and contact details for their own staff. The
documents themselves stay in the vendor's platform. Our category managers, who
work from the corporate network, review what has been uploaded and approve or
reject it.

The portal is a SaaS product. The vendor hosts it and we do not run any part of
it. The vendor's datasheet says the platform is secure by design, that it uses
enterprise-grade encryption throughout, that all access is fully authenticated
and audited, and that the product is fully compliant.

Suppliers sign in with a username and password that the vendor issues to them.

Every night the vendor pushes a supplier data extract to a landing bucket in our
cloud account. Our supplier master service loads that file and writes the
records into the supplier database. The bucket, the service and the database are
all in our cloud account.

We were told the nightly extract is encrypted end to end. The runbook for the
landing bucket says the file arrives as a plain CSV and is picked up as-is.
