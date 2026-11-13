Building permit portal — notes for the security review.

Applicants (homeowners, builders, architects) apply for building permits on the portal from anywhere. Every application request goes through the permit gateway, which faces the internet. The gateway allows 60 requests a minute from one IP address and rejects any request body over 5 MB. It passes each request on to the permit service.

The permit service keeps applications, their status and the inspectors' decisions in the permit database. Marta thinks the permit service limits how many open applications one account can have, but nobody has checked that.

Plan drawings do not go through the gateway, because they are too big. Applicants upload them straight to the plan upload service, which is also on the internet. The plan upload service has no size limit and no rate limit: architects send very large PDFs and we did not want to turn anyone away. It saves each file in the plan store and hands it to the plan renderer.

The plan renderer converts each PDF into image tiles that inspectors can zoom into, and writes the tiles back to the plan store. It runs four conversions at a time; anything more waits.

Inspectors review applications and record their decisions through the permit service from the city network. All inspectors in a district use one shared inspector login.

The permit service, the plan upload service, the plan renderer, the permit database and the plan store run in the city's cloud account.
