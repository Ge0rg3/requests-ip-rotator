import logging
from time import sleep
from typing import List
import concurrent.futures
from random import choice, randint

import ipaddress
import boto3.session
import requests as rq
import botocore.exceptions

from .regions import DEFAULT_REGIONS, EXTRA_REGIONS, ALL_REGIONS  # noqa: F401

logger = logging.getLogger(__name__)

MAX_IPV4 = ipaddress.IPv4Address._ALL_ONES


class ApiGateway(rq.adapters.HTTPAdapter):

    def __init__(
        self,
        site: str,
        regions: List[str] = DEFAULT_REGIONS,
        access_key_id=None,
        access_key_secret=None,
        verbose=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        if site.endswith("/"):
            self.site = site[:-1]
        else:
            self.site = site
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.api_name = site + " - IP Rotate API"
        self.regions = regions
        # `verbose` is accepted for backward compatibility. Configure logging directly
        # for finer control; True maps to DEBUG, False silences info/warning messages.
        if verbose is not None:
            logger.setLevel(logging.DEBUG if verbose else logging.ERROR)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, type, value, traceback):
        self.shutdown()

    def send(
        self,
        request: rq.models.PreparedRequest,
        stream=False,
        timeout=None,
        verify=True,
        cert=None,
        proxies=None,
    ):
        endpoint = choice(self.endpoints)
        protocol, site = request.url.split("://", 1)
        site_path = site.split("/", 1)[1]
        request.url = "https://" + endpoint + "/ProxyStage/" + site_path
        request.headers["Host"] = endpoint

        # Auto generate random X-Forwarded-For if doesn't exist.
        # Otherwise AWS forwards true IP address in X-Forwarded-For header
        x_forwarded_for = request.headers.get("X-Forwarded-For")
        if x_forwarded_for is None:
            x_forwarded_for = ipaddress.IPv4Address._string_from_ip_int(
                randint(0, MAX_IPV4)
            )

        # Move "X-Forwarded-For" to "X-My-X-Forwarded-For". This then gets converted back
        # within the gateway.
        request.headers.pop("X-Forwarded-For", None)
        request.headers["X-My-X-Forwarded-For"] = x_forwarded_for
        return super().send(request, stream, timeout, verify, cert, proxies)

    def init_gateway(self, region: str, force=False, require_manual_deletion=False):
        session = boto3.session.Session()
        awsclient = session.client(
            "apigateway",
            region_name=region,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.access_key_secret,
        )

        # If API gateway already exists for host, return pre-existing endpoint
        if not force:
            try:
                current_apis = ApiGateway.get_gateways(awsclient)
            except botocore.exceptions.ClientError as e:
                if e.response["Error"]["Code"] == "UnrecognizedClientException":
                    logger.warning(
                        f"Could not create region (some regions require manual enabling): {region}"
                    )
                    return {"success": False}
                else:
                    raise e

            for api in current_apis:
                if "name" in api and api["name"].startswith(self.api_name):
                    return {
                        "success": True,
                        "endpoint": f"{api['id']}.execute-api.{region}.amazonaws.com",
                        "new": False,
                    }

        new_api_name = self.api_name
        if require_manual_deletion:
            new_api_name += " (Manual Deletion Required)"

        create_api_response = awsclient.create_rest_api(
            name=new_api_name,
            endpointConfiguration={
                "types": [
                    "REGIONAL",
                ]
            },
        )

        rest_api_id = create_api_response["id"]

        # Get ID for new resource
        get_resource_response = awsclient.get_resources(restApiId=rest_api_id)

        # Create "Resource" (wildcard proxy path)
        create_resource_response = awsclient.create_resource(
            restApiId=rest_api_id,
            parentId=get_resource_response["items"][0]["id"],
            pathPart="{proxy+}",
        )

        # Allow all methods to new resource
        awsclient.put_method(
            restApiId=rest_api_id,
            resourceId=get_resource_response["items"][0]["id"],
            httpMethod="ANY",
            authorizationType="NONE",
            requestParameters={
                "method.request.path.proxy": True,
                "method.request.header.X-My-X-Forwarded-For": True,
            },
        )

        # Make new resource route traffic to new host
        awsclient.put_integration(
            restApiId=rest_api_id,
            resourceId=get_resource_response["items"][0]["id"],
            type="HTTP_PROXY",
            httpMethod="ANY",
            integrationHttpMethod="ANY",
            uri=self.site,
            connectionType="INTERNET",
            requestParameters={
                "integration.request.path.proxy": "method.request.path.proxy",
                "integration.request.header.X-Forwarded-For": "method.request.header.X-My-X-Forwarded-For",
            },
        )

        awsclient.put_method(
            restApiId=rest_api_id,
            resourceId=create_resource_response["id"],
            httpMethod="ANY",
            authorizationType="NONE",
            requestParameters={
                "method.request.path.proxy": True,
                "method.request.header.X-My-X-Forwarded-For": True,
            },
        )

        awsclient.put_integration(
            restApiId=rest_api_id,
            resourceId=create_resource_response["id"],
            type="HTTP_PROXY",
            httpMethod="ANY",
            integrationHttpMethod="ANY",
            uri=f"{self.site}/{{proxy}}",
            connectionType="INTERNET",
            requestParameters={
                "integration.request.path.proxy": "method.request.path.proxy",
                "integration.request.header.X-Forwarded-For": "method.request.header.X-My-X-Forwarded-For",
            },
        )

        # Creates deployment resource, so that our API to be callable
        awsclient.create_deployment(restApiId=rest_api_id, stageName="ProxyStage")

        return {
            "success": True,
            "endpoint": f"{rest_api_id}.execute-api.{region}.amazonaws.com",
            "new": True,
        }

    @staticmethod
    def get_gateways(client):
        gateways = []
        position = None
        complete = False
        while not complete:
            if isinstance(position, str):
                gateways_response = client.get_rest_apis(limit=500, position=position)
            else:
                gateways_response = client.get_rest_apis(limit=500)

            gateways.extend(gateways_response["items"])

            position = gateways_response.get("position", None)
            if position is None:
                complete = True

        return gateways

    def delete_gateway(self, region: str, endpoints=None):
        session = boto3.session.Session()
        awsclient = session.client(
            "apigateway",
            region_name=region,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.access_key_secret,
        )

        endpoint_ids = []
        if endpoints is not None:
            for endpoint in endpoints:
                endpoint_ids.append(endpoint.split(".")[0])

        # Get all gateway apis (or skip if we don't have permission)
        try:
            apis = ApiGateway.get_gateways(awsclient)
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "UnrecognizedClientException":
                return []

        api_iter = 0
        deleted = []
        while api_iter < len(apis):
            api = apis[api_iter]
            if "name" in api and self.api_name == api["name"]:
                try:
                    if endpoints is not None and api["id"] not in endpoint_ids:
                        api_iter += 1
                        continue
                    success = awsclient.delete_rest_api(restApiId=api["id"])
                    if success:
                        deleted.append(api["id"])
                    else:
                        logger.error(f"Failed to delete API {api['id']}.")
                except botocore.exceptions.ClientError as e:
                    err_code = e.response["Error"]["Code"]
                    if err_code == "TooManyRequestsException":
                        sleep(1)
                        continue
                    else:
                        logger.error(f"Failed to delete API {api['id']}.")
            api_iter += 1
        return deleted

    def start(self, force=False, require_manual_deletion=False, endpoints=[]):
        if len(endpoints) > 0:
            self.endpoints = endpoints
            return endpoints

        logger.info(
            f"Starting API gateway{'s' if len(self.regions) > 1 else ''} in {len(self.regions)} regions."
        )
        self.endpoints = []
        new_endpoints = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = []

            # Send each region creation to its own thread
            for region in self.regions:
                futures.append(
                    executor.submit(
                        self.init_gateway,
                        region=region,
                        force=force,
                        require_manual_deletion=require_manual_deletion,
                    )
                )

            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result["success"]:
                    self.endpoints.append(result["endpoint"])
                    if result["new"]:
                        new_endpoints += 1

        logger.info(
            f"Using {len(self.endpoints)} endpoints with name '{self.api_name}' ({new_endpoints} new)."
        )
        return self.endpoints

    def shutdown(self, endpoints=None):
        logger.info(
            f"Deleting gateway{'s' if len(self.regions) > 1 else ''} for site '{self.site}'."
        )
        futures = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            # Send each region deletion to its own thread
            for region in self.regions:
                futures.append(
                    executor.submit(
                        self.delete_gateway, region=region, endpoints=endpoints
                    )
                )
            deleted = []
            for future in concurrent.futures.as_completed(futures):
                deleted += future.result()
        logger.info(f"Deleted {len(deleted)} endpoints for site '{self.site}'.")
        return deleted
