#define SERIAL_PORT_TESTING
#include "../main.cpp"

struct ServerShutdownTest {
    static bool queued_successful_timer(bool parent) {
        Server server;
        if (parent && !server.setup_parent_watch()) {
            std::cerr << "parent watcher setup failed\n";
            return false;
        }
        bool already_queued = false;
        asio::steady_timer stop_timer(server.io);
        stop_timer.expires_after(std::chrono::milliseconds(1));
        stop_timer.async_wait([&](asio::error_code ec) {
            if (ec) return;
            auto& target = parent ? server.parent_timer : server.watchdog;
            already_queued = target.cancel() == 0;
            server.stop();
        });
        if (parent) server.watch_parent();
        else server.watch();
        // Both expirations become ready in one reactor batch, stop first.
        std::this_thread::sleep_for(std::chrono::milliseconds(parent ? 280 : 80));
        server.io.run_for(std::chrono::milliseconds(100));
        bool drained = server.io.stopped();
        server.io.stop();
        if (!already_queued) std::cerr << "test failed to queue successful completion\n";
        if (!drained) std::cerr << (parent ? "parent" : "watchdog") << " timer rearmed after stop\n";
        return already_queued && drained;
    }
};

int main() {
    bool watchdog = ServerShutdownTest::queued_successful_timer(false);
    bool parent = ServerShutdownTest::queued_successful_timer(true);
    return watchdog && parent ? 0 : 1;
}
