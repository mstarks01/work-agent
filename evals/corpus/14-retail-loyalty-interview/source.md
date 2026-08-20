Loyalty points platform, as written down.

Customers collect and spend loyalty points through our mobile app. The points API is a plain REST service and it is the only way anything touches points. The points API is internal-only; the mobile app reaches it through the group's shared gateway.

Stores have self-service kiosks where a customer who paid cash can scan a paper receipt to claim the points on it. The kiosks submit scanned receipts to the points API. The kiosks sit on the store network.

Balances and the transaction history live in the points database. The points API and the points database run on the core network.

Support can adjust a customer's balance by hand when something goes wrong, through an adjustments page the points API serves. Support work out of the Leeds office.

This note is old in places. Priya on the platform team has the current picture; the interview transcript alongside is more recent than this note.
