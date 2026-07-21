Telemetry platform for our deployed sensor fleet.

We have a few thousand sensor nodes installed on customer sites. They are
outside our physical control — a technician can be standing next to one with a
laptop. Each node publishes readings over MQTT to the device gateway, which is
an MQTT broker we run on GKE and expose to the internet because the nodes dial
in from anywhere. Nodes authenticate to the broker with a pre-shared key. The
key is per fleet, not per device. Nobody has rotated it since deployment.

The gateway looks up the device in a device registry (Firestore) to check the
key and to find out which customer the node belongs to. Readings the gateway
accepts are forwarded onto Pub/Sub and picked up by the telemetry normalizer,
a Python consumer in our analytics network, which writes them into the
telemetry lake in BigQuery. The lake has site addresses and occupancy patterns
in it, so it is customer data.

Firmware updates work the other way round: nodes poll a Cloud Storage bucket
for a new image and install what they find. The bucket is public read, because
making the nodes authenticate to it was awkward. I do not know whether the
nodes check a signature on the image before installing it.

Field technicians service nodes over a local serial console. I don't know what
authentication that console has, if any.

Our own fleet operators look at dashboards over BigQuery from the corporate
network, signed in with company SSO.
