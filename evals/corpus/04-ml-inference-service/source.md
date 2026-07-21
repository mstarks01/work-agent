Hosted model inference for our product teams.

Other teams' backends call our inference gateway, a FastAPI service on GKE
that we expose on the internet because two of the calling services are in a
different cloud. Callers pass an API key in a header. Keys are issued per
calling team and we have never expired one.

The gateway forwards the request to the model server, which runs the actual
model on GPU nodes in our model network. There is no auth between the gateway
and the model server; the model network is meant to be reachable only from the
gateway.

The model server loads model artifacts from a model registry bucket at startup.
It uses its own service account for that. I don't believe anything verifies the
artifact hasn't been swapped — we just trust the bucket.

Requests often need customer features, which the model server reads from a
Redis feature store on the same network. Redis has no password on it; it is
only reachable inside the model network. The features include account age and
spend bands per customer.

The gateway writes every request and response into an inference log in
BigQuery, for debugging. That means raw prompts, which sometimes carry whatever
the calling team's users typed.

ML engineers publish new model artifacts to the registry bucket. I'm not sure
what governs who can push — I think it is a shared group account.
