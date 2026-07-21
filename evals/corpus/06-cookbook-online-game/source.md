Online battle-royale game — player-facing flows.

Players run our game client on their own machines. We do not control those
machines. The player launches the client and plays through it.

The client connects out to two things in our production network. It talks to
the lobby on TCP 1234 for matchmaking, and once a match starts it talks
directly to the game servers on TCP 1235. Both of those have to be reachable
from wherever a player is, so they are exposed.

The lobby reads the player database to set up a match, and hands the match over
to the game servers. The game servers read and write the stats database during
and after a match, and they also write back to the player database.

Separately, our customer support staff work from the corporate network and use
a moderation website to look at and act on player accounts. That website reads
and writes the player database directly.

The diagram doesn't record what authentication is on any of these links, or how
any of the stores are protected. The two client links are just port numbers.
