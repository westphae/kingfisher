package pod

import (
	"log"

	"github.com/westphae/kingfisher/internal/location"
	"github.com/westphae/kingfisher/internal/pod/wire"
	"github.com/westphae/kingfisher/internal/sensors"
	"github.com/westphae/kingfisher/internal/store"
)

// logPodSensorAttrs writes a full attr snapshot for each wing sensor device.
func (c *Client) logPodSensorAttrs() {
	if c.st == nil {
		return
	}
	for _, dev := range c.reader.TelemetryDeviceNames() {
		recs := c.reader.FlightLogAttrRecordsForUIDevice(dev)
		if len(recs) == 0 {
			continue
		}
		if err := c.st.LogAttrs(dev, location.Pod, recs); err != nil {
			log.Printf("pod: %s log attrs: %v", dev, err)
			continue
		}
		c.setLoggedAttrs(dev, recs)
	}
}

// logPodSensorAttrDiff logs only attrs that changed since the last snapshot.
func (c *Client) logPodSensorAttrDiff() {
	if c.st == nil {
		return
	}
	for _, dev := range c.reader.TelemetryDeviceNames() {
		curr := c.reader.FlightLogAttrRecordsForUIDevice(dev)
		if len(curr) == 0 {
			continue
		}
		diff := sensors.DiffAttrs(c.loggedAttrs(dev), curr)
		if len(diff) == 0 {
			continue
		}
		if err := c.st.LogAttrs(dev, location.Pod, diff); err != nil {
			log.Printf("pod: %s log attr diff: %v", dev, err)
			continue
		}
		c.setLoggedAttrs(dev, curr)
	}
}

func (c *Client) logPodRateAck(sensor wire.SensorID) {
	if c.st == nil {
		return
	}
	dev := c.reader.deviceNameLocked(sensor)
	if dev == "" {
		return
	}
	curr := c.reader.FlightLogAttrRecordsForUIDevice(dev)
	diff := sensors.DiffAttrs(c.loggedAttrs(dev), curr)
	if len(diff) == 0 {
		return
	}
	if err := c.st.LogAttrs(dev, location.Pod, diff); err != nil {
		log.Printf("pod: %s log rate ack: %v", dev, err)
		return
	}
	c.setLoggedAttrs(dev, curr)
}

func (c *Client) loggedAttrs(dev string) []store.AttrRecord {
	c.loggedMu.Lock()
	defer c.loggedMu.Unlock()
	return c.logged[dev]
}

func (c *Client) setLoggedAttrs(dev string, recs []store.AttrRecord) {
	c.loggedMu.Lock()
	defer c.loggedMu.Unlock()
	if c.logged == nil {
		c.logged = make(map[string][]store.AttrRecord)
	}
	cp := make([]store.AttrRecord, len(recs))
	copy(cp, recs)
	c.logged[dev] = cp
}
