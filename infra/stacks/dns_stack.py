"""
DnsStack — Route53 hosted zone for awsugcbba.org.

Each service gets a subdomain:
  identity.awsugcbba.org
  projects.awsugcbba.org
  messaging.awsugcbba.org
  omnichannel.awsugcbba.org
  admin.awsugcbba.org
  api.awsugcbba.org  (public API gateway)
"""

import aws_cdk as cdk
import aws_cdk.aws_route53 as route53
from constructs import Construct

DOMAIN = "awsugcbba.org"

SUBDOMAINS = [
    "identity",
    "projects",
    "messaging",
    "omnichannel",
    "admin",
    "api",
]


class DnsStack(cdk.Stack):
    """Provisions the Route53 hosted zone and subdomain placeholders."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── Public Hosted Zone ────────────────────────────────────────────────
        self.hosted_zone = route53.PublicHostedZone(
            self,
            "UgsysHostedZone",
            zone_name=DOMAIN,
            comment="ugsys platform — managed by CDK",
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(
            self,
            "HostedZoneId",
            value=self.hosted_zone.hosted_zone_id,
            export_name="UgsysPlatformHostedZoneId",
            description="Route53 hosted zone ID — import in service stacks for ACM validation",
        )
        cdk.CfnOutput(
            self,
            "HostedZoneName",
            value=self.hosted_zone.zone_name,
            export_name="UgsysPlatformHostedZoneName",
        )
        cdk.CfnOutput(
            self,
            "NameServers",
            value=cdk.Fn.join(", ", self.hosted_zone.hosted_zone_name_servers or []),
            description="NS records — update your domain registrar with these",
        )
