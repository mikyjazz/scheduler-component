"""Initialization of Scheduler switch platform."""
import logging
import secrets
import datetime

from homeassistant.helpers.entity import ToggleEntity
import homeassistant.util.dt as dt_util

from homeassistant.components.switch import DOMAIN as PLATFORM
from homeassistant.helpers.service import async_call_from_config
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity_registry import async_entries_for_device
from homeassistant.helpers.device_registry import async_entries_for_config_entry
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
)


from .datacollection import DataCollection
from .const import (
    DOMAIN,
    STATE_INITIALIZING,
    STATE_WAITING,
    STATE_TRIGGERED,
    STATE_DISABLED,
    STATE_INVALID,
)

_LOGGER = logging.getLogger(__name__)


def entity_exists_in_hass(hass, entity_id):
    if hass.states.get(entity_id) is None:
        return False
    else:
        return True

async def async_setup(hass, config):
    """Track states and offer events for binary sensors."""

    return True

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the light from config."""
    _LOGGER.debug("async_setup_platform")
    return True

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Scheduler switch devices. """
    _LOGGER.debug("async_setup_entry")

    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    entities = []

    
    device_registry = await hass.helpers.device_registry.async_get_registry()
    entry = async_entries_for_config_entry(device_registry, config_entry.entry_id)

    if len(entry)>1:
        _LOGGER.error("Found multiple devices for integration")
        return False
    
    device = entry[0]

    entity_registry = await hass.helpers.entity_registry.async_get_registry()
    for entry in async_entries_for_device(entity_registry, device.id):
        entities.append(ScheduleEntity(coordinator, entry.unique_id))
    
    async_add_entities(entities)

    # callback from the coordinator
    def async_add_switch(data):
        """Add switch for Scheduler."""

        """Generate a unique token"""
        token = secrets.token_hex(3)
        while entity_exists_in_hass(hass, "{}.schedule_{}".format(PLATFORM,token)):
            token = secrets.token_hex(3)

        datacollection = DataCollection()
        datacollection.import_from_service(data)

        async_add_entities([ScheduleEntity(coordinator, "schedule_{}".format(token), datacollection)])

    # We add a listener after fetching the data, so manually trigger listener
    coordinator.async_add_listener(async_add_switch)



class ScheduleEntity(RestoreEntity, ToggleEntity):
    """Defines a base schedule entity."""

    def __init__(self, coordinator, entity_id: str, data: DataCollection = None) -> None:
        """Initialize the schedule entity."""
        self.coordinator = coordinator
        self.entity_id = "{}.{}".format(PLATFORM,entity_id)
        self.id = entity_id
        self._name = DOMAIN.title()
        self.dataCollection = data
        self._valid = True
        self._state = STATE_INITIALIZING
        self._timer = None
        self._entry = None

    @property
    def device_info(self) -> dict:
        """Return info for device registry."""
        device = self.coordinator.id
        return {
            "identifiers": {(DOMAIN, device)},
            "name": "Scheduler",
            "model": "Scheduler",
            "sw_version": "v1",
            "manufacturer": "@nielsfaber",
        }

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return self._name

    @property
    def should_poll(self) -> bool:
        """Return the polling requirement of the entity."""
        return False


    @property
    def state(self):
        """Return the state of the entity."""
        return self._state

    @property
    def icon(self):
        """Return icon."""
        return "mdi:calendar-clock"


    @property
    def state_attributes(self):
        """Return the data of the entity."""
        output = self.dataCollection.export_data()
        if self._next_trigger: output["next_trigger"] = self._next_trigger

        return output


    @property
    def available(self):
        """Return True if entity is available."""
        return True

    @property
    def unique_id(self):
        """Return a unique ID to use for this entity."""
        return f"{self.id}"



    async def async_start_timer(self):
        """Search the entries for nearest timepoint and start timer."""
        self._entry = self.dataCollection.get_next_entry(self.coordinator.sun_data)

        timestamp = self.dataCollection.get_timestamp_for_entry(self._entry, self.coordinator.sun_data)
        self._next_trigger = dt_util.as_local(timestamp).isoformat()

        self._timer = async_track_point_in_utc_time(
            self.coordinator.hass, self.async_timer_finished, timestamp
        )
        self._state = STATE_WAITING
        await self.async_update_ha_state()



    async def async_timer_finished(self, time):
        """Callback for timer finished."""

        self._timer = None
        if self._state != STATE_WAITING: return

        _LOGGER.debug("timer for %s is triggered" % self.entity_id)
        self._state = STATE_TRIGGERED
        self._next_trigger = None
        await self.async_update_ha_state()

        # execute the action
        await self.async_execute_command()

        # wait 1 minute before restarting
        now = dt_util.now().replace(microsecond=0)
        next = now + datetime.timedelta(minutes=1)

        self._timer = async_track_point_in_utc_time(
            self.coordinator.hass, self.async_cooldown_timer_finished, next
        )



    async def async_cooldown_timer_finished(self, time):
        """Restart the timer, now that the cooldown timer finished."""
        self._timer = None

        if self._state != STATE_TRIGGERED: return

        await self.async_start_timer()
    
    

    async def async_execute_command(self):
        """Helper to execute command."""
        _LOGGER.debug("async_execute_command")

        service_calls = self.dataCollection.get_service_calls_for_entry(self._entry)
        for service_call in service_calls:
            _LOGGER.debug("executing service %s" % service_call["service"])
            await async_call_from_config(
                self.coordinator.hass, service_call,
            )

    async def async_added_to_hass(self):
        """Connect to dispatcher listening for entity data notifications."""
        await super().async_added_to_hass()

        state = await self.async_get_last_state()

        # Check against None because value can be 0
        if state is not None:
            self._state = state.state
            data = DataCollection()
            self._valid = data.import_data(state.attributes)            
            self.dataCollection = data
            
        await self.async_start_timer()
        

    async def async_update(self):
        """Update Scheduler entity."""
        _LOGGER.debug("async_update")
        await self.coordinator.async_request_refresh()