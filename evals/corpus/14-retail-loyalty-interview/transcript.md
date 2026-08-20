Dan: Thanks for making time, Priya. I am trying to get the loyalty points platform written down properly before the assessment. I have the old platform note in front of me, but I was told half of it has moved on.

Priya: More than half, probably. That note has been wrong since the spring, and it was thin before that. People fix the platform and nobody fixes the paper. What do you want to start with?

Dan: Start with how a customer actually earns points. Forget the diagrams, just walk me through what happens when someone buys something and taps their phone in the app.

Priya: The app on their phone calls the points API. It sends what the customer bought and the API works out the points and writes the new balance. Redeeming is the same call in reverse, the app asks to spend points against a basket and the API says yes or no. From the customer's side it is one tap either way, all the arithmetic is ours.

Dan: And how does the app reach the points API? The note says everything goes through the group's shared gateway.

Priya: That is one of the wrong bits. The phones talk straight to the points API over the internet. The gateway went away last year, when the group platform team wound it down, and we took the direct route rather than build a replacement. Nobody updated the note because nobody owns the note.

Dan: Straight to it. Alright. What does the points API check when a phone calls it? What stops me calling it as somebody else?

Priya: I think it checks a token the app gets at sign-in, but I'd have to look. That code is older than my time on the team and I have never had a reason to open it. It has never come up in an incident, which is the only time we read anything old. Honestly I could not tell you today what it accepts, or what it does with a call it does not like.

Dan: That is fine, an honest gap is more use to me than a guess. What is behind the API?

Priya: The points database. Names, email addresses, balances and the full history of every collect and redeem live in the points database. The API is the only thing that reads or writes it. It writes to the two databases— actually, no. We merged those in the spring. It's one points database now. The old offers database is gone.

Dan: One database, noted. Now the kiosks. The note says a customer who paid cash can scan a paper receipt at a kiosk in the store and claim the points that way.

Priya: That is still true. The kiosks are in every store and they send each receipt to the points API as it is scanned. Cash customers are the whole reason they exist, there is no other way to claim off a paper receipt. The volume is small next to the app but it is steady, mostly older customers who will not install anything.

Dan: Do the kiosks still write straight into the points database, the way the old fleet did?

Priya: The kiosks are Dev's team's area. I couldn't tell you what they talk to these days. I only ever see what arrives at the API.

Dan: I will chase Dev then. Is there anything else that can move a balance? Anything human?

Priya: Support can. When a customer complains, someone on support opens the adjustments page and adds or removes points by hand. Anyone on support uses the same shared login for the adjustments page. It has been that way as long as I have been here, and the password moves around on sticky notes whenever someone new starts.

Dan: The same login for the whole team? So if a balance is adjusted, can you tell me which person did it?

Priya: You can tell it was support. You cannot tell who. It is one account, the page does not ask again, and the history just records that an adjustment happened and by how much. I have raised it before, it always loses to something louder. If a balance ever moves and a customer swears it was not them, we would be guessing between a dozen people.

Dan: Understood. What about load? Does the platform have quiet and busy times?

Priya: Double-points weekends are when it falls over. It has gone down twice this year. Marketing doubles the earn rate for a weekend, every till and every phone hits us at once, and the API is one service with no queue in front of it. When it goes down nobody can collect or spend anything, the app just spins, and the stores get the complaints because the customer is standing there.

Dan: While we are on the API, what is it, technology-wise? The note calls it a plain REST service.

Priya: It's a REST API. JSON in, JSON out. Nothing exotic, no message bus, no second protocol hiding anywhere. The dullness is deliberate, it is the one system here that has to be boring.

Dan: Any partners in the picture? Anyone outside the company who can touch points?

Priya: No. There has been talk for years, it comes back every planning round and dies every planning round. If we ever let the coffee chain redeem points in their app, we'd have to stand something up for them, but nothing like that exists today. Points stay inside the company, earned with us and spent with us.

Dan: Two more and I will let you go. Where does support sit, physically and on the network?

Priya: Leeds office, on the office network like everyone else there. They reach the adjustments page the same way they reach anything internal, there is nothing special about how support gets to it.

Dan: Last one. If you could fix one thing on this platform tomorrow, what would it be?

Priya: The shared support login. Second would be finding out what the API actually checks when a phone calls it, because if the answer is nothing much, the internet can reach it now and I would rather learn that from us than from someone else.

Dan: That is a good place to stop. Thank you, Priya. I will write this up and send it to you to check.

Priya: Send it to Dev's team too. The kiosks deserve their own hour.
