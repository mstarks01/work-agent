School coursework platform — what we run, written up by the school's IT team.

Students hand in their coursework through the coursework service, which is on the internet. Students and teachers sign in with their school accounts. The coursework service checks each request against the user's role: a student can hand in and read work, and only a teacher can mark it.

A student opens, changes and hands in a piece of work again at an address that holds the submission's number. The coursework service checks that the user is a student, but not that the submission is theirs.

The coursework service keeps submissions, marks and comments in the coursework database. Its database account can only read and write the submissions, marks and comments tables.

Office staff manage classes, accounts and final grades in the admin console, which writes them to the coursework database. The admin console runs on the school office network, which is kept apart so that only office staff work in it. Teachers can also reach the admin console from the school network. It checks for the administrator role only on its start page, so a teacher who signs in and opens an admin page's address directly can use that page.

Every Friday the grade export sends final grades from the coursework database over the internet to the district records system. Dana thinks the grade export has read-only access to the database, but she has not looked.

The coursework service, the grade export and the coursework database run in the school's cloud account.
