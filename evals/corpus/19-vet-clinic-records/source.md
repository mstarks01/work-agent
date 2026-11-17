Vet clinic records — notes for the security review, from the clinic group's IT lead.

Pet owners book appointments and read their pets' records in the owner app. The owner app calls the clinic API, which is on the internet. Owners sign in with an email address and a password. When an owner opens a record, the clinic API checks that the pet belongs to that owner's account.

Vets, nurses and receptionists use the clinic desktop app on the clinic network, and it calls the same clinic API. Every staff account can do everything in the clinic API, including deleting medical notes: we never set up roles, because each clinic has only a few staff.

The clinic API keeps owners, pets, appointments and medical notes in the records database. It connects to the records database with the database's admin account.

The partner lab sends test results over the internet to the lab results importer. The importer writes each result into the pet's record in the records database. It does not check which clinic the result is for, or whether the lab may send results for that pet: it writes every result it receives.

The billing service reads appointments from the records database and sends invoices to owners by email. Priya thinks the billing service checks each request against the caller's role, but nobody has confirmed it.

The clinic API, the lab results importer and the billing service run in the group's cloud account. The records database is on the group's private database network, and only those three services can reach it.
