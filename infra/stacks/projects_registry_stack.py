"""
ProjectsRegistryStack — Lambda (container image) + API Gateway + DynamoDB + S3 + CloudWatch.

Service: ugsys-projects-registry

Resources:
  - ECR repository: ugsys-projects-registry
  - DynamoDB table: ugsys-projects-{env}           (projects catalog)
  - DynamoDB table: ugsys-subscriptions-{env}       (volunteer subscriptions)
  - DynamoDB table: ugsys-form-submissions-{env}    (dynamic form responses)
  - S3 bucket: ugsys-images-{env}                   (project images)
  - Lambda function (container image): ugsys-projects-registry-{env}
  - API Gateway HTTP API: ugsys-projects-registry-{env}
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
import aws_cdk.aws_s3 as s3
from constructs import Construct

CUSTOM_DOMAIN = "api.apps.cloud.org.bo"


class ProjectsRegistryStack(cdk.Stack):
    """Provisions all AWS resources for the ugsys-projects-registry service."""

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
        self.ecr_repo = ecr.Repository(
            self,
            "EcrRepo",
            repository_name="ugsys-projects-registry",
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

        # ── DynamoDB — Projects table ──────────────────────────────────────────
        # PK: PROJECT#{id}, SK: PROJECT
        # GSI-1 (status-index): status -> created_at  (list by status)
        # GSI-2 (created_by-index): created_by -> created_at  (owner queries)
        self.projects_table = dynamodb.Table(
            self,
            "ProjectsTable",
            table_name=f"ugsys-projects-{env_name}",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=(
                cdk.RemovalPolicy.RETAIN if env_name == "prod" else cdk.RemovalPolicy.DESTROY
            ),
        )

        # GSI-1: status-based listing
        self.projects_table.add_global_secondary_index(
            index_name="status-index",
            partition_key=dynamodb.Attribute(name="status", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="created_at", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI-2: owner queries
        self.projects_table.add_global_secondary_index(
            index_name="created_by-index",
            partition_key=dynamodb.Attribute(name="created_by", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="created_at", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # ── DynamoDB — Subscriptions table ────────────────────────────────────
        # PK: SUBSCRIPTION#{id}, SK: SUBSCRIPTION
        # GSI-1 (person-index): person_id -> created_at  (list by person)
        # GSI-2 (project-index): project_id -> created_at  (list by project)
        # GSI-3 (person-project-index): person_project_key -> created_at  (uniqueness check)
        self.subscriptions_table = dynamodb.Table(
            self,
            "SubscriptionsTable",
            table_name=f"ugsys-subscriptions-{env_name}",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=(
                cdk.RemovalPolicy.RETAIN if env_name == "prod" else cdk.RemovalPolicy.DESTROY
            ),
        )

        # GSI-1: list subscriptions by person
        self.subscriptions_table.add_global_secondary_index(
            index_name="person-index",
            partition_key=dynamodb.Attribute(name="person_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="created_at", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI-2: list subscriptions by project
        self.subscriptions_table.add_global_secondary_index(
            index_name="project-index",
            partition_key=dynamodb.Attribute(name="project_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="created_at", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI-3: uniqueness check (person+project composite key)
        self.subscriptions_table.add_global_secondary_index(
            index_name="person-project-index",
            partition_key=dynamodb.Attribute(
                name="person_project_key", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(name="created_at", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # ── DynamoDB — Form submissions table ─────────────────────────────────
        # PK: SUBMISSION#{id}, SK: SUBMISSION
        # GSI-1 (project-index): project_id -> created_at  (list by project)
        # GSI-2 (person-project-index): person_project_key -> created_at  (uniqueness)
        self.form_submissions_table = dynamodb.Table(
            self,
            "FormSubmissionsTable",
            table_name=f"ugsys-form-submissions-{env_name}",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=(
                cdk.RemovalPolicy.RETAIN if env_name == "prod" else cdk.RemovalPolicy.DESTROY
            ),
        )

        self.form_submissions_table.add_global_secondary_index(
            index_name="project-index",
            partition_key=dynamodb.Attribute(name="project_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="created_at", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        self.form_submissions_table.add_global_secondary_index(
            index_name="person-project-index",
            partition_key=dynamodb.Attribute(
                name="person_project_key", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(name="created_at", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # ── S3 — Project images bucket ────────────────────────────────────────
        self.images_bucket = s3.Bucket(
            self,
            "ImagesBucket",
            bucket_name=f"ugsys-images-{env_name}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=False,
            removal_policy=(
                cdk.RemovalPolicy.RETAIN if env_name == "prod" else cdk.RemovalPolicy.DESTROY
            ),
            auto_delete_objects=(env_name != "prod"),
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.PUT],
                    allowed_origins=["https://registry.apps.cloud.org.bo"],
                    allowed_headers=["*"],
                    max_age=3000,
                )
            ],
        )

        # ── CloudWatch Log Group (KMS-encrypted) ──────────────────────────────
        log_group = logs.LogGroup(
            self,
            "LambdaLogGroup",
            log_group_name=f"/aws/lambda/ugsys-projects-registry-{env_name}",
            retention=logs.RetentionDays.ONE_MONTH,
            encryption_key=platform_key,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # ── Lambda execution role ─────────────────────────────────────────────
        execution_role = iam.Role(
            self,
            "LambdaExecutionRole",
            role_name=f"ugsys-projects-registry-lambda-{env_name}",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="Execution role for ugsys-projects-registry Lambda",
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

        # DynamoDB access — all three tables
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="DynamoDBProjectsAccess",
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
                    self.projects_table.table_arn,
                    f"{self.projects_table.table_arn}/index/*",
                    self.subscriptions_table.table_arn,
                    f"{self.subscriptions_table.table_arn}/index/*",
                    self.form_submissions_table.table_arn,
                    f"{self.form_submissions_table.table_arn}/index/*",
                ],
            )
        )

        # S3 — presigned URL generation for image uploads
        execution_role.add_to_policy(
            iam.PolicyStatement(
                sid="S3ImageAccess",
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:PutObject",
                    "s3:GetObject",
                    "s3:DeleteObject",
                ],
                resources=[f"{self.images_bucket.bucket_arn}/*"],
            )
        )

        # KMS — allow Lambda to use the platform key for DynamoDB/logs
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
        self.function = lambda_.DockerImageFunction(
            self,
            "LambdaFunction",
            function_name=f"ugsys-projects-registry-{env_name}",
            code=lambda_.DockerImageCode.from_ecr(
                self.ecr_repo,
                tag_or_digest="latest",
            ),
            role=execution_role,
            timeout=cdk.Duration.seconds(30),
            memory_size=512,
            environment={
                "APP_ENV": env_name,
                "ENVIRONMENT": env_name,
                "DYNAMODB_TABLE_PREFIX": "ugsys",
                "PROJECTS_TABLE_NAME": self.projects_table.table_name,
                "SUBSCRIPTIONS_TABLE_NAME": self.subscriptions_table.table_name,
                "FORM_SUBMISSIONS_TABLE_NAME": self.form_submissions_table.table_name,
                "S3_IMAGES_BUCKET": self.images_bucket.bucket_name,
                "EVENT_BUS_NAME": "ugsys-platform-bus",
                "LOG_LEVEL": "INFO",
            },
            log_group=log_group,
            tracing=lambda_.Tracing.ACTIVE,
        )

        # ── API Gateway HTTP API ──────────────────────────────────────────────
        self.api = apigwv2.HttpApi(
            self,
            "HttpApi",
            description="ugsys Projects Registry API",
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
                export_name=f"UgsysProjectsRegistryCustomUrl-{env_name}",
            )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(
            self,
            "ApiUrl",
            value=self.api.api_endpoint,
            export_name=f"UgsysProjectsRegistryApiUrl-{env_name}",
            description="Projects Registry API Gateway endpoint",
        )
        cdk.CfnOutput(
            self,
            "FunctionName",
            value=self.function.function_name,
            export_name=f"UgsysProjectsRegistryFunctionName-{env_name}",
        )
        cdk.CfnOutput(
            self,
            "EcrRepositoryUri",
            value=self.ecr_repo.repository_uri,
            export_name=f"UgsysProjectsRegistryEcrUri-{env_name}",
            description="ECR URI — set as ECR_REPOSITORY_URI secret in the service repo",
        )
        cdk.CfnOutput(
            self,
            "ProjectsTableName",
            value=self.projects_table.table_name,
            export_name=f"UgsysProjectsTable-{env_name}",
        )
        cdk.CfnOutput(
            self,
            "SubscriptionsTableName",
            value=self.subscriptions_table.table_name,
            export_name=f"UgsysSubscriptionsTable-{env_name}",
        )
        cdk.CfnOutput(
            self,
            "FormSubmissionsTableName",
            value=self.form_submissions_table.table_name,
            export_name=f"UgsysFormSubmissionsTable-{env_name}",
        )
        cdk.CfnOutput(
            self,
            "ImagesBucketName",
            value=self.images_bucket.bucket_name,
            export_name=f"UgsysImagesBucket-{env_name}",
        )
