"""
FrontendStack — S3 + CloudFront for the ugsys React SPA frontend.

Domain: registry.cloud.org.bo
Resources:
  - S3 bucket: ugsys-frontend-{env}  (private, OAC-only access)
  - CloudFront OAC (Origin Access Control)
  - CloudFront distribution with:
      - Custom domain: registry.cloud.org.bo (prod) / registry.dev.cloud.org.bo (dev)
      - ACM certificate (us-east-1, passed in as parameter)
      - Cache behaviors: /assets/* → 1yr immutable, default → no-cache
      - Custom error responses: 403/404 → /index.html (200) for SPA routing
      - Response headers policy with strict CSP
  - Route53 A alias record pointing to CloudFront
"""

import aws_cdk as cdk
import aws_cdk.aws_certificatemanager as acm
import aws_cdk.aws_cloudfront as cloudfront
import aws_cdk.aws_cloudfront_origins as origins
import aws_cdk.aws_route53 as route53
import aws_cdk.aws_route53_targets as route53_targets
import aws_cdk.aws_s3 as s3
from constructs import Construct

DOMAIN = "cloud.org.bo"
FRONTEND_SUBDOMAIN = "registry"


class FrontendStack(cdk.Stack):
    """Provisions S3 + CloudFront for the ugsys React SPA."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_name: str,
        hosted_zone: route53.IHostedZone,
        certificate_arn: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        domain_name = (
            f"{FRONTEND_SUBDOMAIN}.{DOMAIN}"
            if env_name == "prod"
            else f"{FRONTEND_SUBDOMAIN}.dev.{DOMAIN}"
        )

        # ── S3 bucket (private — CloudFront OAC only) ─────────────────────────
        self.bucket = s3.Bucket(
            self,
            "FrontendBucket",
            bucket_name=f"ugsys-frontend-{env_name}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=False,
            removal_policy=(
                cdk.RemovalPolicy.RETAIN if env_name == "prod" else cdk.RemovalPolicy.DESTROY
            ),
            auto_delete_objects=(env_name != "prod"),
        )

        # ── Outputs — bucket always available ────────────────────────────────
        cdk.CfnOutput(
            self,
            "BucketName",
            value=self.bucket.bucket_name,
            export_name=f"UgsysFrontendBucket-{env_name}",
            description="S3 bucket — deploy frontend build artifacts here",
        )

        # ── CloudFront + Route53 — only when certificate_arn is provided ──────
        # Deploy with: cdk deploy -c certificate_arn=arn:aws:acm:us-east-1:...
        # Local synth (no cert context) skips these resources safely.
        if not certificate_arn:
            return

        # ── ACM certificate (must be in us-east-1 for CloudFront) ─────────────
        certificate = acm.Certificate.from_certificate_arn(self, "Certificate", certificate_arn)

        # ── Response headers policy (CSP + security headers) ──────────────────
        response_headers_policy = cloudfront.ResponseHeadersPolicy(
            self,
            "SecurityHeadersPolicy",
            response_headers_policy_name=f"ugsys-frontend-security-{env_name}",
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                content_type_options=cloudfront.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cloudfront.ResponseHeadersFrameOptions(
                    frame_option=cloudfront.HeadersFrameOption.DENY,
                    override=True,
                ),
                referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                    referrer_policy=cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                    override=True,
                ),
                strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=cdk.Duration.days(365),
                    include_subdomains=True,
                    preload=True,
                    override=True,
                ),
                xss_protection=cloudfront.ResponseHeadersXSSProtection(
                    protection=False,  # Disabled — CSP is the correct defense
                    override=True,
                ),
                content_security_policy=cloudfront.ResponseHeadersContentSecurityPolicy(
                    content_security_policy=(
                        "default-src 'self'; "
                        f"connect-src 'self' https://api.{DOMAIN}; "
                        "img-src 'self' data: https:; "
                        "style-src 'self' 'unsafe-inline'; "
                        "script-src 'self'; "
                        "font-src 'self'; "
                        "frame-ancestors 'none'; "
                        "base-uri 'self'; "
                        "form-action 'self'"
                    ),
                    override=True,
                ),
            ),
            custom_headers_behavior=cloudfront.ResponseCustomHeadersBehavior(
                custom_headers=[
                    cloudfront.ResponseCustomHeader(
                        header="Permissions-Policy",
                        value="camera=(), microphone=(), geolocation=(), payment=()",
                        override=True,
                    ),
                    cloudfront.ResponseCustomHeader(
                        header="Cross-Origin-Opener-Policy",
                        value="same-origin",
                        override=True,
                    ),
                    cloudfront.ResponseCustomHeader(
                        header="Cross-Origin-Resource-Policy",
                        value="same-origin",
                        override=True,
                    ),
                ]
            ),
        )

        # ── CloudFront distribution ────────────────────────────────────────────
        self.distribution = cloudfront.Distribution(
            self,
            "Distribution",
            comment=f"ugsys frontend — {domain_name}",
            domain_names=[domain_name],
            certificate=certificate,
            default_root_object="index.html",
            # SPA routing: 403/404 from S3 → serve index.html with 200
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=cdk.Duration.seconds(0),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=cdk.Duration.seconds(0),
                ),
            ],
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(self.bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                response_headers_policy=response_headers_policy,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                compress=True,
            ),
            additional_behaviors={
                # Immutable hashed assets — cache 1 year
                "/assets/*": cloudfront.BehaviorOptions(
                    origin=origins.S3BucketOrigin.with_origin_access_control(self.bucket),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                    response_headers_policy=response_headers_policy,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                    compress=True,
                ),
            },
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            enable_logging=True,
        )

        # ── Route53 alias record ──────────────────────────────────────────────
        route53.ARecord(
            self,
            "AliasRecord",
            zone=hosted_zone,
            record_name=domain_name,
            target=route53.RecordTarget.from_alias(
                route53_targets.CloudFrontTarget(self.distribution)
            ),
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(
            self,
            "DistributionId",
            value=self.distribution.distribution_id,
            export_name=f"UgsysFrontendDistributionId-{env_name}",
            description="CloudFront distribution ID — use for cache invalidation in deploy",
        )
        cdk.CfnOutput(
            self,
            "DistributionDomain",
            value=self.distribution.distribution_domain_name,
            export_name=f"UgsysFrontendDistributionDomain-{env_name}",
        )
        cdk.CfnOutput(
            self,
            "FrontendUrl",
            value=f"https://{domain_name}",
            export_name=f"UgsysFrontendUrl-{env_name}",
        )
