import boto3
import botocore.exceptions
import requests as rq
import concurrent.futures
from random import choice, randint
from time import sleep
import ipaddress

MAX_IPV4 = ipaddress.IPv4Address._ALL_ONES

# Region lists that can be imported and used in the ApiGateway class
DEFAULT_REGIONS = [
    "us-east-1", "us-east-2", "us-west-1", "us-west-2",
    "eu-west-1", "eu-west-2", "eu-west-3", "eu-north-1",
    "eu-central-1", "ca-central-1"
]

EXTRA_REGIONS = DEFAULT_REGIONS + [
    "ap-south-1", "ap-northeast-3", "ap-northeast-2",
    "ap-southeast-1", "ap-southeast-2", "ap-northeast-1",
    "sa-east-1"
]

# These regions require manual opt-in from AWS
ALL_REGIONS = EXTRA_REGIONS + [
    "ap-east-1", "af-south-1", "eu-south-1", "me-south-1"
]


# Inherits from HTTPAdapter so that we can edit each request before sending
class ApiGateway(rq.adapters.HTTPAdapter):

    def __init__(self, site, regions=DEFAULT_REGIONS, access_key_id=None, access_key_secret=None):
        super().__init__()
        # Set simple params from constructor
        if site.endswith("/"):
            self.site = site[:-1]
        else:
            self.site = site
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.api_name = site + " - IP Rotate API"
        self.regions = regions

    # Enter and exit blocks to allow "with" clause
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, type, value, traceback):
        self.shutdown()

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        # Get random endpoint
        endpoint = choice(self.endpoints)
        # Replace URL with our endpoint
        protocol, site = request.url.split("://", 1)
        site_path = site.split("/", 1)[1]
        #Set new request url
        request.url = "https://" + endpoint + "/" + site_path
        # Replace host with endpoint host
        request.headers["Host"] = endpoint
        # Auto generate random X-Forwarded-For if doesn't exist.
        # Otherwise AWS forwards true IP address in X-Forwarded-For header
        x_forwarded_for = request.headers.get("X-Forwarded-For")
        if x_forwarded_for is None:
            x_forwarded_for = ipaddress.IPv4Address._string_from_ip_int(randint(0, MAX_IPV4))
        # Move "X-Forwarded-For" to "X-My-X-Forwarded-For". This then gets converted back
        # within the gateway.
        request.headers.pop("X-Forwarded-For", None)
        request.headers["X-My-X-Forwarded-For"] = x_forwarded_for
        # Run original python requests send function
        return super().send(request, stream, timeout, verify, cert, proxies)

    def init_gateway(self, region, force=False):
        # Init client
        session = boto3.session.Session()
        awsclient = session.client(
            "apigatewayv2",
            region_name=region,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.access_key_secret
        )
        # If API gateway already exists for host, return pre-existing endpoint
        if not force:
            try:
                current_apis = awsclient.get_apis()["Items"]
            except botocore.exceptions.ClientError as e:
                if e.response["Error"]["Code"] == "UnrecognizedClientException":
                    return {
                        "success": False
                    }
                else:
                    raise e

            for api in current_apis:
                if self.api_name == api["Name"]:
                    return {
                        "success": True,
                        "endpoint": f"{api['ApiId']}.execute-api.{region}.amazonaws.com",
                        "new": False
                    }

        # Create simple HTTP API resource
        create_api_response = awsclient.create_api(
            ProtocolType='HTTP',
            Name=self.api_name
        )

        http_api_id = create_api_response["ApiId"]

        # Get ID for new resource
        get_api_response = awsclient.get_api(
            ApiId=http_api_id
        )
        # Create "integration"
        create_integration_response = awsclient.create_integration(
            ApiId=http_api_id,
            IntegrationType='HTTP_PROXY',
            IntegrationMethod='ANY',
            IntegrationUri=self.site + '/{proxy}',
            PayloadFormatVersion='1.0'
        )

        # Create "route" (wildcard proxy path)
        create_resource_response = awsclient.create_route(
            ApiId=http_api_id,
            RouteKey='ANY /{proxy+}',
            Target='integrations/' + create_integration_response['IntegrationId']
        )

        # Create "stage"
        create_stage_response = awsclient.create_stage(
            ApiId=http_api_id,
            AutoDeploy=True,
            Description='Default Stage',
            StageName='$default'
        )

        # Creates deployment resource, so that our API to be callable
        """ awsclient.create_deployment(
            restApiId=rest_api_id,
            stageName="ProxyStage"
        ) """

        # Return endpoint name and whether it show it is newly created
        return {
            "success": True,
            "endpoint": f"{http_api_id}.execute-api.{region}.amazonaws.com",
            "new": True
        }

    def delete_gateway(self, region):
        # Create client
        session = boto3.session.Session()
        awsclient = session.client(
            "apigatewayv2",
            region_name=region,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.access_key_secret
        )
        # Get all gateway apis (or skip if we don't have permission)
        try:
            apis = awsclient.get_apis()["Items"]
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "UnrecognizedClientException":
                return 0
        # Delete APIs matching target name
        api_iter = 0
        deleted = 0
        while api_iter < len(apis):
            api = apis[api_iter]
            # Check if hostname matches
            if self.api_name == api["Name"]:
                # Attempt delete
                try:
                    success = awsclient.delete_api(ApiId=api["ApiId"])
                    if success:
                        deleted += 1
                    else:
                        print(f"Failed to delete API {api['ApiId']}.")
                except botocore.exceptions.ClientError as e:
                    # If timeout, retry
                    err_code = e.response["Error"]["Code"]
                    if err_code == "TooManyRequestsException":
                        sleep(1)
                        continue
                    else:
                        print(f"Failed to delete API {api['ApiId']}.")
            api_iter += 1
        return deleted

    def start(self, force=False, endpoints=[]):
        # If endpoints given already, assign and continue
        if len(endpoints) > 0:
            self.endpoints = endpoints
            return endpoints

        # Otherwise, start/locate new endpoints
        print(f"Starting API gateway{'s' if len(self.regions) > 1 else ''} in {len(self.regions)} regions.")
        self.endpoints = []
        new_endpoints = 0

        # Setup multithreading object
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            # Send each region creation to its own thread
            for region in self.regions:
                futures.append(executor.submit(self.init_gateway, region=region, force=force))
            # Get thread outputs
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result["success"]:
                    self.endpoints.append(result["endpoint"])
                    if result["new"]:
                        new_endpoints += 1

        print(f"Using {len(self.endpoints)} endpoints with name '{self.api_name}' ({new_endpoints} new).")
        return self.endpoints

    def shutdown(self):
        print(f"Deleting gateway{'s' if len(self.regions) > 1 else ''} for site '{self.site}'.")
        futures = []

        # Setup multithreading object
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            # Send each region deletion to its own thread
            for region in self.regions:
                futures.append(executor.submit(self.delete_gateway, region=region))
            # Check outputs
            deleted = 0
            for future in concurrent.futures.as_completed(futures):
                deleted += future.result()
        print(f"Deleted {deleted} endpoints with for site '{self.site}'.")