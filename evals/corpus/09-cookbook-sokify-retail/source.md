Sokify order and dispatch — rough notes for the threat model.

Sokify sells socks online. Customers browse and order through our mobile app;
there is no website. The app talks to the web API over HTTP — not HTTPS, it's
on the list.

The web API keeps customers in the user database: name, address, and the card
they paid with. When an order is placed the API hands the order over to SIMS,
our stock and inventory system, which has been running since long before the
app existed.

SIMS does two things with an order. It writes the delivery address into a flat
file kept alongside it — only addresses go in that file, nothing else — and it
sends a dispatch note to the fax gateway, which faxes the customer a
confirmation with their name and address on it. Yes, fax. The gateway dials the
number stored against the order and nobody checks it arrived at the right
place.

Marketing keep the catalogue in a spreadsheet. The macros in it send SQL
statements straight to the web API to change prices and product copy — it was a
stopgap and it is still here. The spreadsheet lives on a marketing laptop in
the office.

Nobody here can tell me what the API does about authentication, or whether the
user database and the flat file are encrypted.
