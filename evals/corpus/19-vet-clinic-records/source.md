Vet clinic records — notes for the security review, from the clinic group's IT lead.

Pet owners book appointments and read their pets' records in the owner app. The owner app calls the owner API, which is on the internet. Owners sign in with an email address and a password. Whenever an owner opens a record or books an appointment, the owner API checks that the pet belongs to that owner's account.

Vets, nurses and receptionists use the clinic desktop app on the clinic network, and it calls the clinic API. The clinic API checks only that a staff account has signed in. Every staff account can do everything in the clinic API, including deleting medical notes: we never set up roles, because each clinic has only a few staff.

Both APIs keep owners, pets, appointments and medical notes in the records database, and both connect to it with the database's admin account.

The lab results importer is on the internet, and the partner lab sends test results to it over the internet. The importer writes each result into the pet's record in the records database. It does not check whether the partner lab may send a result: it writes every result the lab sends.

The billing service reads appointments from the records database and sends invoices to owners by email. Receptionists open owners' invoices in the billing service from the clinic desktop app. Priya thinks the billing service checks each request against the caller's role, but nobody has confirmed it.

The owner API, the clinic API, the lab results importer and the billing service run in the group's cloud account. The records database is on the group's private database network, and only those four services can reach it.
