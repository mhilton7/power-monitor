# Device protocol references

References were retrieved from their official publishers on 2026-07-19 America/Los_Angeles.

- [Espressif ESP32-S3 API reference](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/index.html): stable documentation identified ESP-IDF v6.0.2 during the check. It informs HTTPS clients/servers, networking, storage, SNTP, OTA, and cryptographic implementation in the separate sensor project.
- [Espressif mDNS documentation](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/protocols/mdns.html): mDNS is treated as discovery convenience, never identity or a routing dependency.
- [Peacefair PZEM-004T V4.0](https://en.peacefair.cn/product/772.html): the official product page lists the external 100 A CT form, 80–260 V, 0–23 kW, 0.5% measurement accuracy, 1 Wh energy resolution, 45–65 Hz, and 9600 baud 8N1 TTL interface.

The server does not claim revenue-grade measurement. Hardware pin mapping and electrical safety stay in the sensor project; the server deliberately refuses remote hardware-pin configuration.
