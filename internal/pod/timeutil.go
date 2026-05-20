package pod

import "time"

// timeInThePast returns a Time guaranteed to be in the past. We use it to
// abort a blocked net.UDPConn.ReadFromUDP via SetReadDeadline when the
// transport is being shut down — passing zero would clear the deadline,
// not trip it. time.Unix(1, 0) is "1970-01-01 00:00:01 UTC", which any
// running system has already passed.
func timeInThePast() time.Time { return time.Unix(1, 0) }
