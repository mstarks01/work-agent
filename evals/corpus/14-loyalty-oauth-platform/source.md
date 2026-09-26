Loyalty rewards platform and partner API.

We run a grocery loyalty programme. Members collect points when they shop and
spend them on gift cards. Everything below runs in our own cloud account, apart
from the members' phones and the partner apps.

Members use our mobile app. The app signs a member in through our authorization
server with the authorization code flow and then calls the rewards API with the
access token it gets back. Nobody wrote down whether the app sends a PKCE
challenge. The app keeps a refresh token on the phone. A mobile refresh token
never expires: every time the app uses it, its expiry slides forward another
ninety days.

Partner apps are web applications that other companies run on their own
servers, such as a meal-planning service that shows a member's points. Our
partnerships team registers each partner app by hand and gives it a client ID
and a client secret. The partner app sends that secret in the body of its token
requests. When the team registers a redirect URI for a partner app, the
authorization server accepts any redirect URI that starts with it. The team
gives every partner app every scope, because working out which scopes each
partner needs took too long.

A member who connects a partner app signs in on the authorization server's
sign-in page in their browser and approves a consent screen that lists the
scopes. The browser then returns to the partner app's redirect URI with an
authorization code. An authorization code stays valid for ten minutes. The
member's sign-in on the authorization server is kept in a cookie for thirty
days. Nobody decided an idle timeout for it.

The authorization server keeps member accounts, email addresses and password
hashes in the identity database, together with the authorization codes, refresh
tokens and consents it issues. Members can disconnect a partner app from the
settings screen of the mobile app, which deletes that app's refresh token from
the identity database. An access token that was already issued keeps working
until it expires.

Access tokens are signed JWTs that live for one hour, and they are plain bearer
tokens. The rewards API checks the signature with a public key it downloads
when it starts. It does not check which client or which audience a token was
issued for. The authorization server signs tokens for the support console with
the same key.

The rewards API reads and writes the points ledger, which holds every member's
balance and every points transaction. Redeeming points for a gift card needs
only a valid access token that carries the redeem scope. The ledger records
which client made each change, and not which member approved it. The rewards
API also writes every request it receives, headers included, to the API log
store.

The support console is a web app that closes a member's account. It takes access tokens from the same authorization server, and it is
served on the same public hostname as the rewards API. Closing an account marks
it closed in the identity database. Nothing happens to that member's refresh
tokens, which sit in the same database.

Every connection from a phone, a browser or a partner server uses HTTPS. Nobody
recorded how the rewards API connects to the ledger, how the support console
connects to the identity database, or how any of the three stores is protected
at rest.
