"""
AdminPanelStack — Lambda (container image) + API Gateway + DynamoDB + S3 + CloudFront.

Service: ugsys-admin-panel

Resources:
  - ECR repository: ugsys-admin-panel
  - DynamoDB table: ugsys-admin-audit-log-{env}     (audit log entries)
  - DynamoDB table: ugsys-admin-service-registry-{env} (service registrations)
  - Lambda function (container image): ugsys-admin-panel-{env}  (BFF)
  - API Gateway HTTP API: ugsys-admin-panel-{env}
  - S3 bucket: ugsys-admin-shell-{env}              (React SPA static assets)
  - CloudFront distribution: admin.apps.cloud.org.bo
  - CloudWatch Log Group (KMS-encrypted)
  - IAM execution role (least privilege)

Domain layout:
  admin.apps.cloud.org.bo          → CloudFront → S3 (Admin Shell SPA)
  admin.apps.cloud.org.bo/api/v1/* → CloudFront → API Gateway → Lambda (BFF)
"""

import aws_cdk as cdk
import aws_cdk.aws_apigatewayv2 as apigwv2
import aws_cdk.aws_apigatewayv2_integrations as integrations
import aws_cdk.aws_certificatemanager as acm
import aws_cdk.aws_cloudfront as cloudfront
import aws_cdk.aws_cloudfront_origins as origins
import aws_cdk.aws_dynamodb as dynamodb
import aws_cdk.aws_ecr as ecr
import aws_cdk.aws_iam as iam
import aws_cdk.aws_kms as kms
import aws_cdk.aws_lambda as lambda_
import aws_cdk.aws_logs as logs
import aws_cdk.aws_route53 as route53
import aws_cdk.aws_route53_targets as route53_targets
import aws_cdk.aws_s3 as s3
import aws_cdk.aws_secretsmanager as secretsmanager
from constructs import Construct

CUSTOM_DOMAIN = "admin.apps.cloud.org.bo"
DOMAIN = "apps.cloud.org.bo"


class AdminPanelStack(cdk.Stack):
    """Provisions all AWS resources for the ugsys-admin-panel service."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_name: str,
        ecr_repo: ecr.IRepository,
        platform_key: kms.IKey,
        hosted_zone: route53.IHostedZone,
        certificate_arn: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        domain_name = CUSTOM_DOMAIN if env_name == "prod" else f"admin.dev.{DOMAIN}"

        # ECR repo is created by AdminPanelEcrStack and passed in
        self.ecr_repo = ecr_repo

        # ── DynamoDB — Audit log table ─────────────────────────────────────────
        self.audit_log_table = dynamodb.Table(
            self,
            "AuditLogTable",
            table_name=f"ugsys-admin-audit-log-{env_name}",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=(
                cdk.RemovalPolicy.RETAIN if env_name == "prod" else cdk.RemovalPolicy.DESTROY
            ),
        )

        # GSI: actor_user_id -> timestamp (query audit log by actor)
        self.audit_log_table.add_global_secondary_index(
            index_name="actor-index",
            partition_key=dynamodb.Attribute(
                name="actor_user_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(name="timestamp", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # ── DynamoDB — Service registry table ─────────────────────────────────
        self.service_registry_table = dynamodb.Table(
            self,
            "ServiceRegistryTable",
            table_name=f"ugsys-admin-service-registry-{env_name}",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=(
                cdk.RemovalPolicy.RETAIN if env_name == "prod" else cdk.RemovalPolicy.DESTROY
            ),
        )

        # ── CloudWatch Log Group (KMS-encrypted) ──────────────────────────────
        log_group = logs.LogGroup(
            self,
            "LambdaLogGroup",
            log_group_name=f"/aws/lambda/ugsys-admin-panel-{env_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            encryption_key=platform_key,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # ── Lambda execution role ─────────────────────────────────────────────
        execution_role = iam.Role(
            self,
            "LambdaExecutionRole",
            role_name=f"ugsys-admin-panel-lambda-{env_name}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Execution role for ugsys-admin-panel BFF Lambda",
        )

        execution_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSLambdaBasicExecutionRole"
            )
        )

        # ECR — pull container image
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="ECRPullAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                    "ecr:BatchCheckLayerAvailability",
                ],
                resources=[self.ecr_repo.repository_arn],
            )
        )
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="ECRAuthToken",
                effect=iam.Effect.ALLOW,
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],
            )
        )

        # DynamoDB — audit log + service registry
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="DynamoDBAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                ],
                resources=[
                    self.audit_log_table.table_arn,
                    f"{self.audit_log_table.table_arn}/index/*",
                    self.service_registry_table.table_arn,
                    f"{self.service_registry_table.table_arn}/index/*",
                ],
            )
        )

        # KMS
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="KMSAccess",
                effect=iam.Effect.ALLOW,
                actions=["kms:Decrypt", "kms:GenerateDataKey*", "kms:DescribeKey"],
                resources=[platform_key.key_arn],
            )
        )

        # EventBridge — publish domain events
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="EventBridgePublish",
                effect=iam.Effect.ALLOW,
                actions=["events:PutEvents"],
                resources=[
                    f"arn:aws:events:{self.region}:{self.account}:event-bus/ugsys-platform-bus"
                ],
            )
        )

        # ── Secrets Manager — JWT public key (RS256) ──────────────────────────
        # The public key is stored alongside the private key in the identity-manager
        # JWT keys secret. We read it at synth time via a dynamic reference so that
        # key rotation (updating the secret) is reflected on the next deploy without
        # any stack changes.
        jwt_keys_secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            "JwtKeysSecret",
            f"ugsys-identity-manager-jwt-keys-{env_name}",
        )

        # Grant Lambda read access to the secret.
        jwt_keys_secret.grant_read(execution_role)

        # ── Lambda function (container image) ─────────────────────────────────
        # Pulls from ECR. The deploy workflow ensures a valid image exists in ECR
        # before this stack is deployed (pushes a placeholder on first run if needed).
        self.function = lambda_.DockerImageFunction(
            self,
            "LambdaFunction",
            function_name=f"ugsys-admin-panel-{env_name}",
            code=lambda_.DockerImageCode.from_ecr(
                self.ecr_repo,
                tag_or_digest="latest",
            ),
            role=execution_role,
            timeout=cdk.Duration.seconds(30),
            memory_size=512,
            environment={
                "APP_ENV": env_name,
                "AUDIT_LOG_TABLE_NAME": self.audit_log_table.table_name,
                "SERVICE_REGISTRY_TABLE_NAME": self.service_registry_table.table_name,
                "EVENT_BUS_NAME": "ugsys-platform-bus",
                "LOG_LEVEL": "INFO",
                "JWT_AUDIENCE": "admin-panel",
                "JWT_ISSUER": "ugsys-identity-manager",
                # Identity Manager base URL — used by the BFF to forward auth requests.
                "IDENTITY_MANAGER_BASE_URL": (
                    "https://auth.apps.cloud.org.bo"
                    if env_name == "prod"
                    else f"https://auth.dev.{DOMAIN}"
                ),
                # User Profile Service base URL — used by the BFF to proxy profile requests.
                # API lives at profiles.apps.cloud.org.bo (NOT profile.* which is the SPA frontend).
                "USER_PROFILE_SERVICE_BASE_URL": (
                    "https://profiles.apps.cloud.org.bo"
                    if env_name == "prod"
                    else f"https://profiles.dev.{DOMAIN}"
                ),
                # RS256 public key — resolved from Secrets Manager at deploy time.
                # The Lambda reads this env var to verify JWT signatures.
                "JWT_PUBLIC_KEY": jwt_keys_secret.secret_value_from_json(
                    "public_key"
                ).unsafe_unwrap(),
                "JWT_KEY_ID": jwt_keys_secret.secret_value_from_json("key_id").unsafe_unwrap(),
            },
            log_group=log_group,
            tracing=lambda_.Tracing.ACTIVE,
        )

        # ── API Gateway HTTP API (BFF) ────────────────────────────────────────
        self.api = apigwv2.HttpApi(
            self,
            "HttpApi",
            description="ugsys Admin Panel BFF API",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=[f"https://{domain_name}"],
                allow_methods=[apigwv2.CorsHttpMethod.ANY],
                allow_headers=["Content-Type", "Authorization", "X-Request-ID", "Cookie"],
                allow_credentials=True,
                max_age=cdk.Duration.days(1),
            ),
        )

        # Explicitly list methods — do NOT include OPTIONS.
        # When OPTIONS is included in a /{proxy+} route, API Gateway forwards preflights
        # to Lambda instead of handling them natively via cors_preflight config above.
        self.api.add_routes(
            path="/{proxy+}",
            methods=[
                apigwv2.HttpMethod.GET,
                apigwv2.HttpMethod.POST,
                apigwv2.HttpMethod.PUT,
                apigwv2.HttpMethod.PATCH,
                apigwv2.HttpMethod.DELETE,
                apigwv2.HttpMethod.HEAD,
            ],
            integration=integrations.HttpLambdaIntegration("LambdaIntegration", self.function),
        )

        # ── S3 bucket (Admin Shell SPA) ───────────────────────────────────────
        self.shell_bucket = s3.Bucket(
            self,
            "ShellBucket",
            bucket_name=f"ugsys-admin-shell-{env_name}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=False,
            removal_policy=(
                cdk.RemovalPolicy.RETAIN if env_name == "prod" else cdk.RemovalPolicy.DESTROY
            ),
            # auto_delete_objects intentionally omitted — avoids CDK injecting a
            # Lambda-backed custom resource just to empty the bucket on stack teardown.
            # Empty the bucket manually before destroying the stack in non-prod.
        )

        # ── Outputs always available ──────────────────────────────────────────
        cdk.CfnOutput(
            self,
            "ShellBucketName",
            value=self.shell_bucket.bucket_name,
            export_name=f"UgsysAdminShellBucket-{env_name}",
            description="S3 bucket — sync admin-shell/dist here on deploy",
        )
        cdk.CfnOutput(
            self,
            "FunctionName",
            value=self.function.function_name,
            export_name=f"UgsysAdminPanelFunctionName-{env_name}",
        )
        cdk.CfnOutput(
            self,
            "ApiUrl",
            value=self.api.api_endpoint,
            export_name=f"UgsysAdminPanelApiUrl-{env_name}",
        )

        # ── CloudFront + Route53 — only when certificate_arn is provided ──────
        if not certificate_arn:
            return

        certificate = acm.Certificate.from_certificate_arn(self, "Certificate", certificate_arn)

        # Response headers policy
        response_headers_policy = cloudfront.ResponseHeadersPolicy(
            self,
            "SecurityHeadersPolicy",
            response_headers_policy_name=f"ugsys-admin-security-{env_name}",
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
                    protection=False,
                    override=True,
                ),
                content_security_policy=cloudfront.ResponseHeadersContentSecurityPolicy(
                    content_security_policy=(
                        "default-src 'self'; "
                        f"connect-src 'self' https://{domain_name}/api/v1 https://auth.{DOMAIN}; "
                        "img-src 'self' data: https:; "
                        "style-src 'self' 'unsafe-inline'; "
                        "script-src 'self'; "
                        "font-src 'self' data:; "
                        "frame-ancestors 'none'; "
                        "base-uri 'self'; "
                        "form-action 'self'"
                    ),
                    override=True,
                ),
            ),
        )

        # API Gateway origin (BFF)
        api_origin = origins.HttpOrigin(
            f"{self.api.api_id}.execute-api.{self.region}.amazonaws.com",
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
        )

        # CloudFront distribution
        self.distribution = cloudfront.Distribution(
            self,
            "Distribution",
            comment=f"ugsys admin panel — {domain_name}",
            domain_names=[domain_name],
            certificate=certificate,
            default_root_object="index.html",
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
            # Default: serve SPA from S3
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(self.shell_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                response_headers_policy=response_headers_policy,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                compress=True,
            ),
            additional_behaviors={
                # Hashed assets — cache 1 year
                "/assets/*": cloudfront.BehaviorOptions(
                    origin=origins.S3BucketOrigin.with_origin_access_control(self.shell_bucket),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                    response_headers_policy=response_headers_policy,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
                    compress=True,
                ),
                # BFF API — forward to Lambda, no caching
                "/api/v1/*": cloudfront.BehaviorOptions(
                    origin=api_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    compress=False,
                ),
            },
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            enable_logging=True,
        )

        # Route53 alias
        route53.ARecord(
            self,
            "AliasRecord",
            zone=hosted_zone,
            record_name=domain_name,
            target=route53.RecordTarget.from_alias(
                route53_targets.CloudFrontTarget(self.distribution)
            ),
        )

        cdk.CfnOutput(
            self,
            "DistributionId",
            value=self.distribution.distribution_id,
            export_name=f"UgsysAdminPanelDistributionId-{env_name}",
            description="CloudFront distribution ID — use for cache invalidation on deploy",
        )
        cdk.CfnOutput(
            self,
            "AdminPanelUrl",
            value=f"https://{domain_name}",
            export_name=f"UgsysAdminPanelUrl-{env_name}",
        )
