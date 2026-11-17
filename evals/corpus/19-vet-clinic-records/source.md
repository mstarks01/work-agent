Vet clinic records — notes for the security review, from the clinic group's IT lead.

Pet owners book appointments and read their pets' records in the owner app. The owner app calls the clinic API, which is on the internet. Owners sign in with an email address and a password. Whenever an owner opens a record or books an appointment, the clinic API checks that the pet belongs to that owner's account.

Vets, nurses and receptionists use the clinic desktop app on the clinic network, and it calls the same clinic API. For staff requests, the clinic API checks only that the account has signed in. Every staff account can do everything in the clinic API, including deleting medical notes: we never set up roles, because each clinic has only a few staff.

The clinic API keeps owners, pets, appointments and medical notes in the records database. It connects to the records database with the database's admin account.

The lab results importer is on the internet, and the partner lab sends test results to it over the internet. The importer writes each result into the pet's record in the records database. It does not check which clinic a result is for, or whether the lab may send it: it writes every result the partner lab sends.

The billing service reads appointments from the records database and sends invoices to owners by email. Receptionists open owners' invoices in the billing service from the clinic desktop app. Priya thinks the billing service checks each request against the caller's role, but nobody has confirmed it.

The clinic API, the lab results importer and the billing service run in the group's cloud account. The records database is on the group's private database network, and only those three services can reach it.
