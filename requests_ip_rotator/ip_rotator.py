import boto3
import botocore.exceptions
import requests as rq
import concurrent.futures
from random import choice
from time import sleep
from urllib.parse import parse_qs, urlparse

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

    def modify_request_dict(self, request):
        # Get random endpoint
        endpoint = choice(self.endpoints)
        # Replace URL with our endpoint
        parsed_url = urlparse(request['url'])
        params_in_url = parse_qs(parsed_url.query)
        if params_in_url:
            if 'params' in request:
                # params in request take priority over querystring
                request['params'] = params_in_url.update(request['params'])
            else:
                request['params'] = params_in_url

        site_path = parsed_url.path
        request['url'] = "https://" + endpoint + "/ProxyStage/" + site_path
        # Replace host with endpoint host
        request['headers']["Host"] = endpoint
        return request

    def modify_request_obj(self, request):
        request_dict = {
            'url': request.url,
            'params': request.params,    
            'headers': request.headers,
        }
        modified_request_dict = self.request_dict(request_dict)
        request.url = modified_request_dict['url']
        request.params = modified_request_dict['params']
        request.headers = modified_request_dict['params']
        return request

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        return super().send(self.modify_request_obj(request), stream, timeout, verify, cert, proxies)

    def init_gateway(self, region, force=False):
        # Init client
        session = boto3.session.Session()
        awsclient = session.client(
            "apigateway",
            region_name=region,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.access_key_secret
        )
        # If API gateway already exists for host, return pre-existing endpoint
        if not force:
            try:
                current_apis = awsclient.get_rest_apis()["items"]
            except botocore.exceptions.ClientError as e:
                if e.response["Error"]["Code"] == "UnrecognizedClientException":
                    print(f"Could not create region (some regions require manual enabling): {region}")
                    return {
                        "success": False
                    }
                else:
                    raise e

            for api in current_apis:
                if self.api_name == api["name"]:
                    return {
                        "success": True,
                        "endpoint": f"{api['id']}.execute-api.{region}.amazonaws.com",
                        "new": False
                    }

        # Create simple rest API resource
        create_api_response = awsclient.create_rest_api(
            name=self.api_name,
            endpointConfiguration={
                "types": [
                    "REGIONAL",
                ]
            }
        )

        # Get ID for new resource
        get_resource_response = awsclient.get_resources(
            restApiId=create_api_response["id"]
        )
        rest_api_id = create_api_response["id"]

        # Create "Resource" (wildcard proxy path)
        create_resource_response = awsclient.create_resource(
            restApiId=create_api_response["id"],
            parentId=get_resource_response["items"][0]["id"],
            pathPart="{proxy+}"
        )

        # Allow all methods to new resource
        awsclient.put_method(
            restApiId=create_api_response["id"],
            resourceId=get_resource_response["items"][0]["id"],
            httpMethod="ANY",
            authorizationType="NONE",
            requestParameters={
                "method.request.path.proxy": True,
                "method.request.header.X-My-X-Forwarded-For": True
            }
        )

        # Make new resource route traffic to new host
        awsclient.put_integration(
            restApiId=create_api_response["id"],
            resourceId=get_resource_response["items"][0]["id"],
            type="HTTP_PROXY",
            httpMethod="ANY",
            integrationHttpMethod="ANY",
            uri=self.site,
            connectionType="INTERNET",
            requestParameters={
                "integration.request.path.proxy": "method.request.path.proxy",
                "integration.request.header.X-Forwarded-For": "method.request.header.X-My-X-Forwarded-For"
            }
        )

        awsclient.put_method(
            restApiId=create_api_response["id"],
            resourceId=create_resource_response["id"],
            httpMethod="ANY",
            authorizationType="NONE",
            requestParameters={
                "method.request.path.proxy": True,
                "method.request.header.X-My-X-Forwarded-For": True
            }
        )

        awsclient.put_integration(
            restApiId=create_api_response["id"],
            resourceId=create_resource_response["id"],
            type="HTTP_PROXY",
            httpMethod="ANY",
            integrationHttpMethod="ANY",
            uri=f"{self.site}/{{proxy}}",
            connectionType="INTERNET",
            requestParameters={
                "integration.request.path.proxy": "method.request.path.proxy",
                "integration.request.header.X-Forwarded-For": "method.request.header.X-My-X-Forwarded-For"
            }
        )

        # Creates deployment resource, so that our API to be callable
        awsclient.create_deployment(
            restApiId=rest_api_id,
            stageName="ProxyStage"
        )
       
        # Return endpoint name and whether it show it is newly created
        return {
            "success": True,
            "endpoint": f"{rest_api_id}.execute-api.{region}.amazonaws.com",
            "new": True
        }

    def delete_gateway(self, region):
        # Create client
        session = boto3.session.Session()
        awsclient = session.client('apigateway',
                                   region_name=region,
                                   aws_access_key_id=self.access_key_id,
                                   aws_secret_access_key=self.access_key_secret
                                   )
        # Get all gateway apis (or skip if we don't have permission)
        try:
            apis = awsclient.get_rest_apis()["items"]
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "UnrecognizedClientException":
                return 0
        # Delete APIs matching target name
        api_iter = 0
        deleted = 0
        while api_iter < len(apis):
            api = apis[api_iter]
            # Check if hostname matches
            if self.api_name == api["name"]:
                # Attempt delete
                try:
                    success = awsclient.delete_rest_api(restApiId=api["id"])
                    if success:
                        deleted += 1
                    else:
                        print(f"Failed to delete API {api['id']}.")
                except botocore.exceptions.ClientError as e:
                    # If timeout, retry
                    err_code = e.response["Error"]["Code"]
                    if err_code == "TooManyRequestsException":
                        sleep(1)
                        continue
                    else:
                        print(f"Failed to delete API {api['id']}.")
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
