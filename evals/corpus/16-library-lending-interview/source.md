Library lending service, as written down.

Members borrow, renew and reserve books through the library app on their phones. The lending API is a plain REST service and it is the only way anything touches loans. The lending API is internal-only.

Every branch has self-service kiosks where a member scans their library card and the books they are borrowing or returning. The kiosks send each loan and each return to the lending API. The kiosks sit on the branch network.

Members, loans and reservations live in the loans database. The lending API and the loans database run in the council data centre.

Desk staff can extend a loan, clear a block on a member's account or mark a book as returned by hand, through a circulation page the lending API serves. Desk staff work on the branch network.
