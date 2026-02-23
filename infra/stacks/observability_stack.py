"""
ObservabilityStack — Centralized CloudWatch dashboards and alarms.

Aggregates metrics from all ugsys services into a single ops dashboard.
"""

import aws_cdk as cdk
import aws_cdk.aws_cloudwatch as cw
import aws_cdk.aws_events as events
import aws_cdk.aws_kms as kms
import aws_cdk.aws_logs as logs
from constructs import Construct


class ObservabilityStack(cdk.Stack):
    """Provisions centralized CloudWatch dashboards and log groups."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        event_bus: events.EventBus,
        kms_key: kms.IKey,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── Centralized log groups per service ────────────────────────────────
        services = [
            "identity-manager",
            "admin-panel",
            "projects-registry",
            "mass-messaging",
            "omnichannel-service",
        ]

        for svc in services:
            logs.LogGroup(
                self,
                f"LogGroup-{svc}",
                log_group_name=f"/ugsys/{svc}/api",
                encryption_key=kms_key,
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=cdk.RemovalPolicy.DESTROY,
            )

        # ── Platform Dashboard ────────────────────────────────────────────────
        dashboard = cw.Dashboard(
            self,
            "UgsysPlatformDashboard",
            dashboard_name="ugsys-platform",
        )

        dashboard.add_widgets(
            cw.TextWidget(
                markdown="# ugsys Platform — Operations Dashboard",
                width=24,
                height=2,
            ),
            cw.GraphWidget(
                title="EventBridge — Events Published",
                left=[
                    cw.Metric(
                        namespace="AWS/Events",
                        metric_name="MatchedEvents",
                        dimensions_map={"EventBusName": event_bus.event_bus_name},
                        statistic="Sum",
                        period=cdk.Duration.minutes(5),
                    )
                ],
                width=12,
            ),
            cw.GraphWidget(
                title="EventBridge — Failed Invocations",
                left=[
                    cw.Metric(
                        namespace="AWS/Events",
                        metric_name="FailedInvocations",
                        dimensions_map={"EventBusName": event_bus.event_bus_name},
                        statistic="Sum",
                        period=cdk.Duration.minutes(5),
                    )
                ],
                width=12,
            ),
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(
            self,
            "DashboardUrl",
            value="https://console.aws.amazon.com/cloudwatch/home#dashboards:name=ugsys-platform",
            description="CloudWatch platform dashboard URL",
        )
