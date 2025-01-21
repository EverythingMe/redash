import boto3
import pandas as pd
from redash.query_runner import BaseQueryRunner, register
from redash.query_runner import TYPE_STRING, TYPE_INTEGER, TYPE_BOOLEAN, TYPE_FLOAT, TYPE_DATE, TYPE_DATETIME
from redash.utils import json_dumps, json_loads
import logging

TYPES_MAP = {
    "bool": TYPE_BOOLEAN,
    "datetime64[ns]": TYPE_DATETIME,
    "datetime64[s]": TYPE_DATETIME,
    "float64": TYPE_FLOAT,
    "int64": TYPE_INTEGER,
    "object": TYPE_STRING
}

logger = logging.getLogger(__name__)

class S3(BaseQueryRunner):
    @classmethod
    def name(cls):
        return "Amazon S3"
    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "region": {"type": "string", "title": "AWS Region"},
                "bucket_name": {"type": "string", "title": "Bucket Name"},
                "object_key": {"type": "string", "title": "Object Key"}
            },
            "required": ["region", "bucket_name", "object_key"],
            "order": ["region", "bucket_name", "object_key"],
        }
    def test_connection(self):
        region = self.configuration["region"]
        bucket_name = self.configuration["bucket_name"]
        object_key = self.configuration["object_key"]

        # Set S3 client using Boto3
        s3_client = boto3.client("s3")

        query = "SELECT * from S3Object"
        # As of now we are required to pass in the object key so we are configuring the data source to a particular S3 object temporarily
        resp = s3_client.select_object_content(
            Bucket=bucket_name,
            Key= object_key, # We need the CSV file (Object Key)
            ExpressionType='SQL',
            Expression=query,
            InputSerialization = {'CSV': {"FileHeaderInfo": "Use"}, 'CompressionType': 'NONE'},
            OutputSerialization = {'JSON': {}},
        )

        # Need to first deploy this to see how response data schema is before we can parse it into rows/columns
        for event in resp['Payload']:
            if 'Records' in event:
                records = event['Records']['Payload']
                logger.info("Records: %s", records)

    def run_query(self, query, user):
        region = self.configuration["region"]
        bucket_name = self.configuration["bucket_name"]
        object_key = self.configuration["object_key"]

        # Set S3 client using Boto3
        s3_client = boto3.client("s3")

        # As of now we are required to pass in the object key so we are configuring the data source to a particular S3 object temporarily
        resp = s3_client.select_object_content(
            Bucket=bucket_name,
            Key= object_key, # We need the CSV file (Object Key)
            ExpressionType='SQL',
            Expression=query,
            InputSerialization = {'CSV': {"FileHeaderInfo": "Use"}, 'CompressionType': 'NONE'},
            OutputSerialization = {'JSON': {}},
        )

        # Need to first deploy this to see how response data schema is before we can parse it into rows/columns
        json_result = ""
        for event in resp['Payload']:
            if 'Records' in event:
                json_result = event['Records']['Payload']
                logger.info("Records: %s", json_result)
        
        json_result = json_result.decode('utf8')
        json_result = json_result.replace('\n', '')
        json_result = json_result.replace('\\r', '')
        json_result = json_result.replace('}{', '},{')
        json_result = "[" + json_result + "]"
        logger.info("JSON: %s", json_result)
        dict_result = json_loads(json_result)
        logger.info("DictResult: %s", dict_result)
        df = pd.DataFrame(dict_result)
        logger.info("DataFrame: %s", df.to_string())
        columns = []
        rows = df.to_dict('records')

        for col in df.columns:
            columns.append(
                {
                    "name": col,
                    "friendly_name": col,
                    "type": TYPES_MAP[str(df[col].dtype)]
                }
            )
        
        # Returning the query results in Redash format
        data = {"columns": columns, "rows": rows}
        error = None
        json_data = json_dumps(data)
        return json_data, error
    
# Registering custom S3 query runner
register(S3)