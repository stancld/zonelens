from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import serializers

from api.models import ActivityType, CustomZonesConfig, HeartRateZone, ZoneSummary

if TYPE_CHECKING:
	from typing import Any


class HeartRateZoneSerializer(serializers.ModelSerializer):
	class Meta:
		model = HeartRateZone
		fields = ["id", "name", "min_hr", "max_hr", "order"]
		read_only_fields = ["id"]


class CustomZonesConfigSerializer(serializers.ModelSerializer):
	zones_definition = HeartRateZoneSerializer(many=True)
	user = serializers.PrimaryKeyRelatedField(read_only=True)
	activity_type = serializers.ChoiceField(choices=ActivityType.choices, required=True)

	class Meta:
		model = CustomZonesConfig
		fields = ["id", "user", "activity_type", "zones_definition", "created_at", "updated_at"]
		read_only_fields = ["id", "created_at", "updated_at"]

	def create(self, validated_data: dict[str, Any]) -> CustomZonesConfig:
		zones_data = validated_data.pop("zones_definition")
		# Associate with the authenticated user, not from payload
		user = self.context["request"].user.strava_profile
		config = CustomZonesConfig.objects.create(user=user, **validated_data)
		for zone_data in zones_data:
			HeartRateZone.objects.create(config=config, **zone_data)
		return config

	def update(
		self, instance: CustomZonesConfig, validated_data: dict[str, Any]
	) -> CustomZonesConfig:
		zones_data = validated_data.pop("zones_definition", None)

		instance.activity_type = validated_data.get("activity_type", instance.activity_type)
		instance.save()

		if zones_data is not None:
			# Simple approach: delete existing and create new ones
			# More sophisticated updates could match by ID or order
			instance.zones_definition.all().delete()
			for zone_data in zones_data:
				HeartRateZone.objects.create(config=instance, **zone_data)

		return instance


class ZoneSummarySerializer(serializers.ModelSerializer):
	class Meta:
		model = ZoneSummary
		fields = [
			"id",
			"user",
			"period_type",
			"year",
			"period_index",
			"zone_times_seconds",
			"updated_at",
		]
		read_only_fields = ["id", "user", "updated_at"]
