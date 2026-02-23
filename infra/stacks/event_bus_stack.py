"""
EventBusStack — Shared EventBridge custom bus for all ugsys services.

All microservices publish and subscribe to this bus.
The bus ARN is exported as a CloudFormation output so other stacks can import it.
"""

import aws_cdk as cdk
import aws_cdk.aws_events as events
import aws_cdk.aws_logs as logs
from constructs import Construct


class EventBusStack(cdk.Stack):
    """Provisions the shared ugsys EventBridge custom bus."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── Custom Event Bus ──────────────────────────────────────────────────
        self.bus = events.EventBus(
            self,
            "UgsysPlatformBus",
            event_bus_name="ugsys-platform-bus",
        )

        # ── Archive: retain all events for 30 days (replay / debugging) ──────
        self.bus.archive(
            "UgsysPlatformBusArchive",
            archive_name="ugsys-platform-bus-archive",
            description="30-day archive of all ugsys platform events",
            event_pattern=events.EventPattern(source=[events.Match.prefix("ugsys.")]),
            retention=cdk.Duration.days(30),
        )

        # ── CloudWatch log group for event debugging ──────────────────────────
        logs.LogGroup(
            self,
            "UgsysBusLogs",
            log_group_name="/ugsys/platform/event-bus",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(
            self,
            "EventBusName",
            value=self.bus.event_bus_name,
            export_name="UgsysPlatformBusName",
            description="Shared EventBridge bus name — import in service stacks",
        )
        cdk.CfnOutput(
            self,
            "EventBusArn",
            value=self.bus.event_bus_arn,
            export_name="UgsysPlatformBusArn",
            description="Shared EventBridge bus ARN",
        )
