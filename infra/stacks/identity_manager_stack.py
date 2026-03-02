"""
IdentityManagerStack — Lambda (container image) + API Gateway + DynamoDB + CloudWatch.

Resources:
  - ECR repository: ugsys-identity-manager
  - DynamoDB table: ugsys-identity-manager-users-{env}
  - DynamoDB table: ugsys-identity-{env}-token-blacklist  (JWT revocation, TTL-enabled)
  - Secrets Manager secret: ugsys-identity-manager-jwt-secret-{env}
  - Lambda function (container image): ugsys-identity-manager-{env}
  - API Gateway HTTP API: ugsys-identity-manager-{env}
  - CloudWatch Log Group (KMS-encrypted)
  - IAM execution role (least privilege)
"""

import aws_cdk as cdk
import aws_cdk.aws_apigatewayv2 as apigwv2
import aws_cdk.aws_apigatewayv2_integrations as integrations
import aws_cdk.aws_certificatemanager as acm
import aws_cdk.aws_dynamodb as dynamodb
import aws_cdk.aws_ecr as ecr
import aws_cdk.aws_iam as iam
import aws_cdk.aws_kms as kms
import aws_cdk.aws_lambda as lambda_
import aws_cdk.aws_logs as logs
import aws_cdk.aws_route53 as route53
import aws_cdk.aws_route53_targets as route53_targets
import aws_cdk.aws_secretsmanager as secretsmanager
from constructs import Construct

CUSTOM_DOMAIN = "auth.apps.cloud.org.bo"


class IdentityManagerStack(cdk.Stack):
    """Provisions all AWS resources for the ugsys-identity-manager service."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        env_name: str,
        platform_key: kms.IKey,
        hosted_zone: route53.IHostedZone,
        certificate_arn: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── ECR Repository ────────────────────────────────────────────────────
        # Service CI/CD pushes container images here; Lambda pulls from it.
        # Bootstrap: push at least one image tagged "latest" before first cdk deploy.
        self.ecr_repo = ecr.Repository(
            self,
            "EcrRepo",
            repository_name="ugsys-identity-manager",
            image_scan_on_push=True,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Keep last 10 images",
                    max_image_count=10,
                    tag_status=ecr.TagStatus.ANY,
                )
            ],
            removal_policy=(
                cdk.RemovalPolicy.RETAIN if env_name == "prod" else cdk.RemovalPolicy.DESTROY
            ),
        )

        # ── DynamoDB — Users table ─────────────────────────────────────────────
        self.users_table = dynamodb.Table(
            self,
            "UsersTable",
            table_name=f"ugsys-identity-manager-users-{env_name}",
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

        # GSI: email -> user lookup
        self.users_table.add_global_secondary_index(
            index_name="email-index",
            partition_key=dynamodb.Attribute(name="email", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # ── DynamoDB — Token blacklist table ──────────────────────────────────
        # Stores revoked JWT JTIs. TTL attribute auto-expires entries at token expiry.
        self.token_blacklist_table = dynamodb.Table(
            self,
            "TokenBlacklistTable",
            table_name=f"ugsys-identity-{env_name}-token-blacklist",
            partition_key=dynamodb.Attribute(name="jti", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            time_to_live_attribute="ttl",
            removal_policy=(
                cdk.RemovalPolicy.RETAIN if env_name == "prod" else cdk.RemovalPolicy.DESTROY
            ),
        )

        # ── Secrets Manager — JWT signing secret ──────────────────────────────
        jwt_secret = secretsmanager.Secret(
            self,
            "JwtSecret",
            secret_name=f"ugsys-identity-manager-jwt-secret-{env_name}",
            description="JWT HS256 signing secret for ugsys-identity-manager",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template="{}",
                generate_string_key="jwt_secret_key",
                password_length=64,
                exclude_punctuation=True,
            ),
            encryption_key=platform_key,
            removal_policy=(
                cdk.RemovalPolicy.RETAIN if env_name == "prod" else cdk.RemovalPolicy.DESTROY
            ),
        )

        # ── CloudWatch Log Group (KMS-encrypted) ──────────────────────────────
        log_group = logs.LogGroup(
            self,
            "LambdaLogGroup",
            log_group_name=f"/aws/lambda/ugsys-identity-manager-{env_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            encryption_key=platform_key,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # ── Lambda execution role ─────────────────────────────────────────────
        execution_role = iam.Role(
            self,
            "LambdaExecutionRole",
            role_name=f"ugsys-identity-manager-lambda-{env_name}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Execution role for ugsys-identity-manager Lambda",
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

        # DynamoDB access — users table
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="DynamoDBUsersAccess",
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
                    self.users_table.table_arn,
                    f"{self.users_table.table_arn}/index/*",
                ],
            )
        )

        # DynamoDB access — token blacklist table
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="DynamoDBTokenBlacklistAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:DeleteItem",
                    "dynamodb:Query",
                ],
                resources=[self.token_blacklist_table.table_arn],
            )
        )

        # KMS — allow Lambda to use the platform key for DynamoDB/logs/secrets
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="KMSAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "kms:Decrypt",
                    "kms:GenerateDataKey*",
                    "kms:DescribeKey",
                ],
                resources=[platform_key.key_arn],
            )
        )

        # Secrets Manager — read JWT secret only
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="SecretsManagerJwtSecret",
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[jwt_secret.secret_arn],
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

        # ── Lambda function (container image) ─────────────────────────────────
        # CI/CD pushes a new image to ECR then calls:
        #   aws lambda update-function-code --image-uri <ecr-uri>:<sha>
        # CDK only manages the function config; image updates happen outside CDK.
        self.function = lambda_.DockerImageFunction(
            self,
            "LambdaFunction",
            function_name=f"ugsys-identity-manager-{env_name}",
            code=lambda_.DockerImageCode.from_ecr(
                self.ecr_repo,
                tag_or_digest="latest",
            ),
            role=execution_role,
            timeout=cdk.Duration.seconds(30),
            memory_size=512,
            environment={
                "APP_ENV": env_name,
                "AWS_ACCOUNT_ID": self.account,
                "DYNAMODB_TABLE_NAME": self.users_table.table_name,
                "TOKEN_BLACKLIST_TABLE_NAME": self.token_blacklist_table.table_name,
                "EVENT_BUS_NAME": "ugsys-platform-bus",
                "LOG_LEVEL": "INFO",
                "JWT_ALGORITHM": "HS256",
                "SECRETS_MANAGER_JWT_SECRET_ARN": jwt_secret.secret_arn,
            },
            log_group=log_group,
            tracing=lambda_.Tracing.ACTIVE,
        )

        # ── API Gateway HTTP API ──────────────────────────────────────────────
        self.api = apigwv2.HttpApi(
            self,
            "HttpApi",
            description="ugsys Identity Manager API",
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=["https://registry.apps.cloud.org.bo"],
                allow_methods=[apigwv2.CorsHttpMethod.ANY],
                allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
                max_age=cdk.Duration.days(1),
            ),
        )

        self.api.add_routes(
            path="/{proxy+}",
            methods=[apigwv2.HttpMethod.ANY],
            integration=integrations.HttpLambdaIntegration("LambdaIntegration", self.function),
        )

        # ── Custom domain ─────────────────────────────────────────────────────
        if certificate_arn:
            certificate = acm.Certificate.from_certificate_arn(self, "Certificate", certificate_arn)
            domain = apigwv2.DomainName(
                self,
                "DomainName",
                domain_name=CUSTOM_DOMAIN,
                certificate=certificate,
            )
            apigwv2.ApiMapping(self, "ApiMapping", api=self.api, domain_name=domain)
            route53.ARecord(
                self,
                "AliasRecord",
                zone=hosted_zone,
                record_name=CUSTOM_DOMAIN,
                target=route53.RecordTarget.from_alias(
                    route53_targets.ApiGatewayv2DomainProperties(
                        domain.regional_domain_name, domain.regional_hosted_zone_id
                    )
                ),
            )
            cdk.CfnOutput(
                self,
                "CustomDomainUrl",
                value=f"https://{CUSTOM_DOMAIN}",
                export_name=f"UgsysIdentityManagerCustomUrl-{env_name}",
            )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(
            self,
            "ApiUrl",
            value=self.api.api_endpoint,
            export_name=f"UgsysIdentityManagerApiUrl-{env_name}",
            description="Identity Manager API Gateway endpoint",
        )
        cdk.CfnOutput(
            self,
            "FunctionName",
            value=self.function.function_name,
            export_name=f"UgsysIdentityManagerFunctionName-{env_name}",
        )
        cdk.CfnOutput(
            self,
            "EcrRepositoryUri",
            value=self.ecr_repo.repository_uri,
            export_name=f"UgsysIdentityManagerEcrUri-{env_name}",
            description="ECR URI — set as ECR_REPOSITORY_URI secret in the service repo",
        )
        cdk.CfnOutput(
            self,
            "UsersTableName",
            value=self.users_table.table_name,
            export_name=f"UgsysIdentityManagerUsersTable-{env_name}",
        )
        cdk.CfnOutput(
            self,
            "TokenBlacklistTableName",
            value=self.token_blacklist_table.table_name,
            export_name=f"UgsysIdentityManagerTokenBlacklistTable-{env_name}",
        )
        cdk.CfnOutput(
            self,
            "JwtSecretArn",
            value=jwt_secret.secret_arn,
            export_name=f"UgsysIdentityManagerJwtSecretArn-{env_name}",
            description="JWT signing secret ARN — do not log this value",
        )
