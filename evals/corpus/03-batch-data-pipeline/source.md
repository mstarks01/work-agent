Nightly partner data pipeline.

Three insurance partners drop a daily extract for us. They push files into a
landing bucket over SFTP. Each partner has a static key we issued when they
onboarded; the keys have not changed since. The extracts contain claim records
with member names and dates of birth.

An Airflow scheduler running in the landing network wakes up at 02:00, lists
the bucket, and reads whatever is there. It does not check that a file came
from the partner whose folder it landed in. Airflow keeps its connection
strings and the partner keys in its own metadata database.

For each file the scheduler triggers a Spark transform job in the warehouse
network. The transform normalizes the records and loads them into BigQuery.
Nothing validates the row contents beyond the schema.

Analysts query the warehouse directly. They are on the warehouse network and
authenticate with SSO, but the grant is dataset-wide — we have not split
member-identifying columns out.

I don't know how the landing bucket is encrypted or whether the Airflow
metadata database is encrypted at rest. Both are on defaults as far as I know.
