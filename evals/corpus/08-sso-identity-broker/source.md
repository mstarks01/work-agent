Colleague sign-in and the identity broker.

Everything colleagues use signs them in through one identity broker. The broker
runs on the corporate network. Colleagues sign in to it from wherever they are,
including their own devices at home.

Once the broker has signed a colleague in it issues them a token. The token
carries the colleague's staff id and a list of the groups they are in. Every
application takes that token and decides what the colleague may do from the
groups in it. Nothing calls back to the broker to ask whether a colleague is
still allowed in.

The store admin console is one of those applications. It also runs on the
corporate network. If the token has the store-manager group in it, the console
lets the holder change prices and void transactions. The console does not check
which store the colleague belongs to, so a store-manager token works against
every store.

The broker signs tokens with a signing key it keeps in a key store. The same
key signs the tokens for every application. Applications fetch the public half
from the broker to check the signature.

The broker keeps the colleagues and the groups they are in its own directory.
Group membership is not maintained there by hand — it comes from the HR system
overnight. The broker pulls the changes once a night, and that is also how
leavers stop being colleagues. Tokens are good for twelve hours and there is no
way to pull one back before it expires.

We also let the franchise stores in. Franchise colleagues do not have staff
accounts with us; their own identity provider vouches for them and the broker
takes that as a sign-in. We have not written down which colleagues that provider
is allowed to vouch for.

The broker writes sign-ins to an audit log. Nobody has written down what that
log records, whether it covers the franchise route, or whether the directory or
the log are encrypted where they sit. Nor has anyone written down whether
colleagues are asked for a second factor.
