Dan: Thanks for making time, Priya. I am trying to get the lending service written down properly before the assessment. I have the old service note in front of me, but I was told half of it has moved on.

Priya: More than half, probably. That note has been wrong since the spring, and it was thin before that. People fix the service and nobody fixes the paper. What do you want to start with?

Dan: Start with how a member actually borrows a book from their phone. Forget the diagrams, just walk me through what happens when someone renews a loan or reserves a title in the app.

Priya: The app on their phone calls the lending API. It says which book and which member, and the API checks the loan rules and writes the change. Reserving is the same shape, the app asks for a title and the API puts the member in the queue for it.

Dan: And how does the app reach the lending API? The note says the lending API is internal-only.

Priya: That is one of the wrong bits. The phones talk straight to the lending API over the internet. We opened it up last year when the app moved off the council's old portal, and nobody updated the note because nobody owns the note.

Dan: Straight to it. Alright. What does the lending API check when a phone calls it? What stops me renewing books as somebody else?

Priya: I think it checks a token the app gets at sign-in, but I'd have to look. That code is older than my time on the team and I have never had a reason to open it. Honestly I could not tell you today what it accepts, or what it does with a call it does not like.

Dan: That is fine, an honest gap is more use to me than a guess. What is behind the API?

Priya: The loans database. Names, email addresses, home addresses and every member's full borrowing history live in the loans database. The API is the only thing that reads or writes it. It writes to the two databases— actually, no. We merged those in the spring. It's one loans database now. The old reservations database is gone.

Dan: One database, noted. Now the kiosks. The note says a member scans their card and their books at a kiosk in the branch.

Priya: That is still true. The kiosks are in every branch and they send each loan and each return to the lending API as it is scanned. Most of the borrowing in a branch goes through them now, the desk mostly handles problems.

Dan: Do the kiosks still write straight into the loans database, the way the old ones did?

Priya: The kiosks are Dev's team's area. I couldn't tell you what they talk to these days. I only ever see what arrives at the API.

Dan: I will chase Dev then. Is there anything else that can change a loan? Anything human?

Priya: Desk staff can. When a member says they returned a book and the record says they did not, someone at the desk opens the circulation page and marks it returned, or clears the block on the account. Everyone at the desk uses the same shared login for the circulation page. The password is on a card taped under the desk.

Dan: The same login for everyone? So if a loan is marked returned, can you tell me which person did it?

Priya: You can tell it was the desk. You cannot tell who, or even which branch. It is one account, the page does not ask again, and the history just records that the loan was closed by hand. If a member swears a book went back and the book never turns up, we would be guessing between forty people.

Dan: Understood. What about load? Does the service have quiet and busy times?

Priya: The launch day of the summer reading challenge is when it falls over. It went down twice last summer. Every school sends its classes in the same week, every kiosk and every phone hits us at once, and the API is one service with no queue in front of it. When it goes down nobody can borrow or return anything, and the branches get the complaints.

Dan: While we are on the API, what is it, technology-wise? The note calls it a plain REST service.

Priya: It's a REST API. JSON in, JSON out. Nothing exotic, no message bus, no second protocol hiding anywhere.

Dan: Any partners in the picture? Anyone outside the library who can touch loans?

Priya: No. There has been talk for years, it comes back every planning round and dies every planning round. If we ever join the regional interlibrary scheme, we'd have to stand something up for the other libraries, but nothing like that exists today. Loans stay inside the service.

Dan: Last one. If you could fix one thing on this service tomorrow, what would it be?

Priya: The shared desk login. Second would be finding out what the API actually checks when a phone calls it, because if the answer is nothing much, the internet can reach it now and I would rather learn that from us than from someone else.

Dan: That is a good place to stop. Thank you, Priya. I will write this up and send it to you to check.

Priya: Send it to Dev's team too. The kiosks deserve their own hour.
