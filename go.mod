module github.com/westphae/kingfisher

go 1.25.0

replace github.com/westphae/go-iio => ../go-iio

replace github.com/westphae/goflying => ../goflying

replace github.com/westphae/geomag => ../geomag

require (
	github.com/gorilla/websocket v1.5.3
	github.com/stratoberry/go-gpsd v1.3.0
	github.com/westphae/geomag v0.0.0-00010101000000-000000000000
	github.com/westphae/go-iio v0.2.0
	github.com/westphae/goflying v0.0.0-00010101000000-000000000000
	modernc.org/sqlite v1.50.1
)

require (
	github.com/dustin/go-humanize v1.0.1 // indirect
	github.com/google/uuid v1.6.0 // indirect
	github.com/mattn/go-isatty v0.0.20 // indirect
	github.com/ncruces/go-strftime v1.0.0 // indirect
	github.com/remyoudompheng/bigfft v0.0.0-20230129092748-24d4a6f8daec // indirect
	github.com/skelterjohn/go.matrix v0.0.0-20130517144113-daa59528eefd // indirect
	golang.org/x/sys v0.42.0 // indirect
	modernc.org/libc v1.72.3 // indirect
	modernc.org/mathutil v1.7.1 // indirect
	modernc.org/memory v1.11.0 // indirect
)
