#include <asio.hpp>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <deque>
#include <filesystem>
#include <iostream>
#include <map>
#include <mutex>
#include <set>
#include <thread>
#ifdef _WIN32
#include <windows.h>
#include <tlhelp32.h>
#include <fcntl.h>
#include <io.h>
#else
#include <fcntl.h>
#include <poll.h>
#include <unistd.h>
#endif

using J = nlohmann::ordered_json;
using Clock = std::chrono::steady_clock;
constexpr size_t frame_limit = 65536, write_limit = 32768, queue_limit = 262144;
constexpr size_t output_limit = 8 * 1024 * 1024;
const char* usage = "expected serve, list, echo --text <UTF-8 text> or --version";
J error_value(const std::string& code, const std::string& message) {
    return J{{"code", code}, {"message", message}};
}
std::string encode(const unsigned char* p, size_t n) {
    static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    for (size_t i = 0; i < n; i += 3) {
        unsigned v = unsigned(p[i]) << 16;
        if (i + 1 < n) v |= unsigned(p[i + 1]) << 8;
        if (i + 2 < n) v |= p[i + 2];
        out += alphabet[(v >> 18) & 63]; out += alphabet[(v >> 12) & 63];
        out += i + 1 < n ? alphabet[(v >> 6) & 63] : '=';
        out += i + 2 < n ? alphabet[v & 63] : '=';
    }
    return out;
}
std::vector<unsigned char> decode(const std::string& s) {
    static const std::string alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    if (s.size() % 4 || s.size() > 4 * ((write_limit + 2) / 3))
        throw std::runtime_error("invalid or oversized base64");
    std::vector<unsigned char> out;
    for (size_t i = 0; i < s.size(); i += 4) {
        unsigned v = 0; size_t padding = 0;
        for (size_t k = 0; k < 4; ++k) {
            auto c = s[i + k];
            if (c == '=') {
                if (k < 2 || i + 4 != s.size()) throw std::runtime_error("invalid base64 padding");
                ++padding; v <<= 6;
            } else {
                auto at = alphabet.find(c);
                if (at == std::string::npos || padding) throw std::runtime_error("invalid base64");
                v = (v << 6) | unsigned(at);
            }
        }
        out.push_back(static_cast<unsigned char>(v >> 16));
        if (padding < 2) out.push_back(static_cast<unsigned char>(v >> 8));
        if (!padding) out.push_back(static_cast<unsigned char>(v));
    }
    if (out.size() > write_limit || encode(out.data(), out.size()) != s)
        throw std::runtime_error("noncanonical or oversized base64");
    return out;
}
std::string text(const J& j, const char* key) {
    if (!j.contains(key) || !j[key].is_string()) throw std::runtime_error(std::string(key) + " must be Text");
    auto s = j[key].get<std::string>();
    if (s.empty() || s.find('\0') != std::string::npos) throw std::runtime_error(std::string(key) + " must be nonempty without NUL");
    return s;
}
int integer(const J& j, const char* key, int lo, int hi) {
    if (!j.contains(key) || !j[key].is_number_integer()) throw std::runtime_error(std::string(key) + " must be integer");
    auto n = j[key].get<int64_t>();
    if (n < lo || n > hi) throw std::runtime_error(std::string(key) + " out of range");
    return int(n);
}
J ports() {
    J list = J::array();
#ifdef _WIN32
    HKEY key;
    auto status = RegOpenKeyExW(HKEY_LOCAL_MACHINE, L"HARDWARE\\DEVICEMAP\\SERIALCOMM", 0, KEY_READ, &key);
    if (status == ERROR_FILE_NOT_FOUND) return list;
    if (status != ERROR_SUCCESS) throw std::runtime_error("serial registry unavailable");
    std::set<std::string> names;
    for (DWORD index = 0;; ++index) {
        wchar_t name[512], value[512]; DWORD nn = 512, vn = sizeof(value), type = 0;
        auto rc = RegEnumValueW(key, index, name, &nn, nullptr, &type, reinterpret_cast<BYTE*>(value), &vn);
        if (rc == ERROR_NO_MORE_ITEMS) break;
        if (rc != ERROR_SUCCESS || type != REG_SZ || vn < sizeof(wchar_t)) continue;
        value[511] = 0;
        int length = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, nullptr, 0, nullptr, nullptr);
        if (length <= 1) continue;
        std::string s(length, '\0');
        WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, s.data(), length, nullptr, nullptr);
        s.pop_back(); names.insert(s);
    }
    RegCloseKey(key);
    for (auto& s : names) list.push_back(J{{"path", "\\\\.\\" + s}, {"name", s}});
#else
    std::set<std::string> names;
    for (const auto& entry : std::filesystem::directory_iterator("/dev")) {
        auto name = entry.path().filename().string();
        if (name.rfind("cu.", 0) == 0 || name.rfind("tty.", 0) == 0)
            names.insert(entry.path().string());
    }
    for (auto& s : names) list.push_back(J{{"path", s}, {"name", std::filesystem::path(s).filename().string()}});
#endif
    return list;
}

// Only the engine thread touches ports/requests. Pipe threads use bounded queues.
class Server {
    struct Write {
        std::string id;
        std::vector<unsigned char> data;
        asio::steady_timer timer;
        std::string failure;
        Write(asio::io_context& io) : timer(io) {}
    };
    struct Port {
        asio::serial_port serial;
        std::string id;
        std::array<unsigned char, 4096> read{};
        std::deque<std::shared_ptr<Write>> writes;
        size_t bytes = 0;
        bool closed = false;
        Port(asio::io_context& io, std::string identity) : serial(io), id(std::move(identity)) {}
    };
    asio::io_context io;
    asio::steady_timer lifetime{io}, watchdog{io}, parent_timer{io};
    asio::signal_set signals{io, SIGINT, SIGTERM};
    std::map<std::string, std::shared_ptr<Port>> connections;
    std::set<std::string> pending;
    uint64_t next_connection = 0;
    size_t total_output = 0;
    std::atomic<bool> stopping{false};
    std::atomic<bool> pipe_failed{false};
    std::atomic<int64_t> writer_started{0};
    std::mutex mutex;
    std::condition_variable cv;
    std::deque<std::string> output;
    size_t output_bytes = 0, ingress = 0;
    std::thread reader, writer;
    int exit_status = 0;
#ifdef _WIN32
    HANDLE parent_handle = nullptr;
#else
    pid_t parent_pid = getppid();
#endif
    static int64_t now() { return std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now().time_since_epoch()).count(); }
    void enqueue(const J& j) {
        auto line = j.dump(-1, ' ', true, J::error_handler_t::replace) + "\n";
        std::lock_guard<std::mutex> lock(mutex);
        if (output_bytes + line.size() > queue_limit) { pipe_failed = true; return; }
        output_bytes += line.size(); output.push_back(std::move(line)); cv.notify_all();
    }
    void emit(const J& j) {
        if (stopping) return;
        auto bytes = j.dump(-1, ' ', true, J::error_handler_t::replace).size() + 1;
        if (total_output + bytes > output_limit - 1024) { limit("output_limit"); return; }
        total_output += bytes; enqueue(j);
    }
    void reply(const std::string& id, const J& value, bool ok = true) {
        pending.erase(id);
        J j{{"protocol", 1}, {"id", id}, {"ok", ok}};
        j[ok ? "result" : "error"] = value; emit(j);
    }
    void event(const std::string& type, const std::string& connection, const J& detail = J::object()) {
        J j{{"protocol", 1}, {"event", type}, {"connection", connection}};
        for (auto it = detail.begin(); it != detail.end(); ++it) j[it.key()] = it.value();
        emit(j);
    }
    void close_port(std::shared_ptr<Port> p, const std::string& reason) {
        if (p->closed) return;
        p->closed = true;
        asio::error_code ec; p->serial.cancel(ec); p->serial.close(ec);
        connections.erase(p->id);
        // The active callback owns its transferred-byte count; queued requests never transferred.
        for (size_t i = 0; i < p->writes.size(); ++i) {
            auto w = p->writes[i]; w->timer.cancel();
            if (i == 0) { if (w->failure.empty()) w->failure = reason; }
            else {
                auto e = error_value(w->failure.empty() ? reason : w->failure, "connection closed; delivery may be partial");
                e["transferred"] = 0; reply(w->id, e, false);
            }
        }
        if (p->writes.size() > 1) p->writes.erase(p->writes.begin() + 1, p->writes.end());
        event("disconnected", p->id, J{{"error", error_value(reason, "connection closed")}});
    }
    void stop(int status = 0) {
        if (stopping.exchange(true)) return;
        exit_status = status;
        while (!connections.empty()) close_port(connections.begin()->second, "session_closed");
        lifetime.cancel(); watchdog.cancel(); parent_timer.cancel(); signals.cancel(); cv.notify_all();
    }
    void limit(const std::string& reason) {
        enqueue(J{{"protocol", 1}, {"event", "session_limit"}, {"connection", ""},
                  {"error", error_value(reason, "session ended; start a new helper explicitly")}});
        stop();
    }
    void read_port(const std::shared_ptr<Port>& p) {
        p->serial.async_read_some(asio::buffer(p->read), [this, p](asio::error_code ec, size_t n) {
            if (p->closed || stopping) return;
            if (n) event("data", p->id, J{{"data", encode(p->read.data(), n)}});
            if (ec) {
                event("error", p->id, J{{"error", error_value("read_failed", ec.message())}});
                close_port(p, "read_failed");
            } else read_port(p);
        });
    }
    void start_write(const std::shared_ptr<Port>& p) {
        if (p->closed || p->writes.empty()) return;
        auto w = p->writes.front();
        asio::async_write(p->serial, asio::buffer(w->data), [this, p, w](asio::error_code ec, size_t n) {
            w->timer.cancel();
            p->bytes -= w->data.size(); p->writes.pop_front();
            if (ec || !w->failure.empty()) {
                auto e = error_value(w->failure.empty() ? "write_failed" : w->failure,
                                     "write incomplete; transferred bytes may have reached device");
                e["transferred"] = n; reply(w->id, e, false);
                close_port(p, "write_failed");
            } else reply(w->id, J{{"connection", p->id}, {"transferred", n}});
            start_write(p);
        });
    }
    void configure(const std::shared_ptr<Port>& p, const J& config) {
        if (!config.is_object()) throw std::runtime_error("config must be Object");
        int baud = integer(config, "baudRate", 1, 4000000);
        int bits = integer(config, "dataBits", 5, 8);
        using S = asio::serial_port_base;
        auto stops = S::stop_bits::one;
        if (config.contains("stopBits") && config["stopBits"].is_string()) {
            auto value = text(config, "stopBits");
            if (value == "two") stops = S::stop_bits::two;
            else if (value == "onePointFive") stops = S::stop_bits::onepointfive;
            else if (value != "one") throw std::runtime_error("invalid stopBits");
        } else {
            stops = integer(config, "stopBits", 1, 2) == 1 ? S::stop_bits::one : S::stop_bits::two;
        }
        auto parity = text(config, "parity"), flow = text(config, "flowControl");
        if (parity != "none" && parity != "odd" && parity != "even") throw std::runtime_error("invalid parity");
        if (flow != "none" && flow != "software" && flow != "hardware") throw std::runtime_error("invalid flowControl");
        p->serial.set_option(S::baud_rate(baud));
        p->serial.set_option(S::character_size(bits));
        p->serial.set_option(S::stop_bits(stops));
        p->serial.set_option(S::parity(parity == "none" ? S::parity::none : parity == "odd" ? S::parity::odd : S::parity::even));
        p->serial.set_option(S::flow_control(flow == "none" ? S::flow_control::none : flow == "software" ? S::flow_control::software : S::flow_control::hardware));
    }
    void request(const std::string& line) {
        if (stopping) return;
        std::string id;
        try {
            unsigned depth = 0;
            bool string = false, escape = false;
            for (char c : line) {
                if (string) {
                    if (escape) escape = false;
                    else if (c == '\\') escape = true;
                    else if (c == '"') string = false;
                } else if (c == '"') string = true;
                else if (c == '{' || c == '[') {
                    if (++depth > 64) throw std::runtime_error("JSON nesting exceeds 64 levels");
                } else if ((c == '}' || c == ']') && depth) --depth;
            }
            auto r = J::parse(line);
            if (!r.is_object()) throw std::runtime_error("request must be Object");
            id = text(r, "id");
            if (id.size() > 128) throw std::runtime_error("id exceeds 128 UTF-8 bytes");
            if (pending.count(id)) { emit(J{{"protocol", 1}, {"id", id}, {"ok", false}, {"error", error_value("duplicate_id", "id already pending")}}); return; }
            if (integer(r, "protocol", 1, 1) != 1) return;
            auto op = text(r, "op");
            if (op == "shutdown") { reply(id, J::object()); stop(); return; }
            if (op == "list") { reply(id, J{{"ports", ports()}}); return; }
            if (op == "cancel") {
                auto target = text(r, "requestId");
                std::shared_ptr<Port> found;
                for (auto& [key, p] : connections) for (auto& w : p->writes)
                    if (w->id == target) { w->failure = "cancelled"; found = p; }
                if (!found) { reply(id, error_value("request_not_found", "no pending write with that id"), false); return; }
                close_port(found, "cancelled"); reply(id, J{{"requestId", target}, {"cancelled", true}}); return;
            }
            if (pending.size() >= 128) { reply(id, error_value("pending_limit", "128 writes already pending"), false); return; }
            if (op == "open") {
                if (connections.size() >= 16) { reply(id, error_value("port_limit", "16 connections already open"), false); return; }
                auto path = text(r, "path");
                auto p = std::make_shared<Port>(io, "connection-" + std::to_string(++next_connection));
                try { p->serial.open(path); configure(p, r.at("config")); }
                catch (...) { asio::error_code ec; p->serial.close(ec); throw; }
                connections[p->id] = p;
                reply(id, J{{"connection", p->id}}); read_port(p); return;
            }
            auto connection = text(r, "connection");
            auto it = connections.find(connection);
            if (it == connections.end()) { reply(id, error_value("connection_not_found", "unknown or closed connection"), false); return; }
            auto p = it->second;
            if (op == "close") { close_port(p, "closed"); reply(id, J{{"connection", connection}}); return; }
            if (op == "configure") {
                try { configure(p, r.at("config")); }
                catch (...) { close_port(p, "configure_failed"); throw; }
                reply(id, J{{"connection", connection}}); return;
            }
            if (op == "write") {
                if (!r.contains("data") || !r["data"].is_string()) throw std::runtime_error("data must be base64 Text");
                auto bytes = decode(r["data"].get<std::string>());
                auto timeout = integer(r, "timeoutMs", 1, 3600000);
                if (p->bytes + bytes.size() > queue_limit) {
                    reply(id, error_value("write_queue_limit", "connection write queue exceeds 256KiB"), false);
                    close_port(p, "write_queue_limit"); return;
                }
                auto w = std::make_shared<Write>(io); w->id = id; w->data = std::move(bytes);
                p->bytes += w->data.size(); p->writes.push_back(w); pending.insert(id);
                w->timer.expires_after(std::chrono::milliseconds(timeout));
                w->timer.async_wait([this, p, w](asio::error_code ec) {
                    if (!ec && !p->closed) { w->failure = "timeout"; close_port(p, "timeout"); }
                });
                if (p->writes.size() == 1) start_write(p);
                return;
            }
            reply(id, error_value("invalid_operation", "unknown op"), false);
        } catch (const asio::system_error& e) { reply(id, error_value("serial_error", e.what()), false); }
          catch (const std::exception& e) { reply(id, error_value("invalid_request", e.what()), false); }
    }
    void input_loop() {
        std::string frame;
        std::array<char, 4096> buffer;
        while (!stopping) {
            int count = 0;
#ifdef _WIN32
            DWORD available = 0;
            auto handle = GetStdHandle(STD_INPUT_HANDLE);
            if (!PeekNamedPipe(handle, nullptr, 0, nullptr, &available, nullptr)) {
                asio::post(io, [this] { stop(); }); return;
            }
            if (!available) { std::this_thread::sleep_for(std::chrono::milliseconds(10)); continue; }
            DWORD read = 0;
            if (!ReadFile(handle, buffer.data(), DWORD(std::min<size_t>(available, buffer.size())), &read, nullptr)) {
                asio::post(io, [this] { stop(); }); return;
            }
            count = int(read);
#else
            pollfd fd{STDIN_FILENO, POLLIN, 0};
            int ready = poll(&fd, 1, 50);
            if (ready < 0) { if (errno == EINTR) continue; pipe_failed = true; return; }
            if (!ready) continue;
            count = int(::read(STDIN_FILENO, buffer.data(), buffer.size()));
            if (count < 0 && (errno == EINTR || errno == EAGAIN)) continue;
#endif
            if (count <= 0) {
                asio::post(io, [this, incomplete = !frame.empty()] {
                    if (incomplete) reply("", error_value("invalid_frame", "EOF before LF"), false);
                    stop();
                }); return;
            }
            for (int i = 0; i < count && !stopping; ++i) {
                if (buffer[i] == '\n') {
                    std::unique_lock<std::mutex> lock(mutex);
                    cv.wait(lock, [this] { return stopping || ingress < 128; });
                    if (stopping) return;
                    ++ingress; lock.unlock();
                    asio::post(io, [this, line = std::move(frame)] {
                        request(line);
                        std::lock_guard<std::mutex> guard(mutex); --ingress; cv.notify_all();
                    }); frame.clear();
                } else {
                    frame += buffer[i];
                    if (frame.size() > frame_limit) {
                        asio::post(io, [this] { reply("", error_value("frame_limit", "frame exceeds 65536 bytes"), false); stop(2); });
                        return;
                    }
                }
            }
        }
    }
    void output_loop() {
#ifndef _WIN32
        int flags = fcntl(STDOUT_FILENO, F_GETFL);
        if (flags < 0 || fcntl(STDOUT_FILENO, F_SETFL, flags | O_NONBLOCK) < 0) { pipe_failed = true; return; }
#endif
        for (;;) {
            std::unique_lock<std::mutex> lock(mutex);
            cv.wait(lock, [this] { return stopping || pipe_failed || !output.empty(); });
            if (pipe_failed || (output.empty() && stopping)) return;
            auto line = std::move(output.front()); output.pop_front(); lock.unlock();
            writer_started = now();
            size_t offset = 0;
            while (offset < line.size()) {
#ifdef _WIN32
                DWORD n = 0;
                if (!WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), line.data() + offset, DWORD(line.size() - offset), &n, nullptr) || !n) { pipe_failed = true; return; }
                offset += n;
#else
                pollfd fd{STDOUT_FILENO, POLLOUT, 0};
                auto ready = poll(&fd, 1, 50);
                if (ready < 0 && errno == EINTR) continue;
                if (now() - writer_started > 2000 || ready < 0) { pipe_failed = true; return; }
                if (!ready) continue;
                auto n = ::write(STDOUT_FILENO, line.data() + offset, line.size() - offset);
                if (n < 0 && (errno == EINTR || errno == EAGAIN)) continue;
                if (n <= 0) { pipe_failed = true; return; }
                offset += size_t(n);
#endif
            }
            writer_started = 0;
            lock.lock(); output_bytes -= line.size();
        }
    }
    void watch() {
        watchdog.expires_after(std::chrono::milliseconds(50));
        watchdog.async_wait([this](asio::error_code ec) {
            if (ec) return;
            if (pipe_failed || (writer_started && now() - writer_started > 2000)) {
                pipe_failed = true; stop(3);
#ifdef _WIN32
                CancelSynchronousIo(writer.native_handle());
#endif
            } else watch();
        });
    }
    bool setup_parent_watch() {
#ifdef _WIN32
        HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if (snapshot == INVALID_HANDLE_VALUE) return false;
        PROCESSENTRY32W entry{};
        entry.dwSize = sizeof(entry);
        DWORD parent = 0;
        if (Process32FirstW(snapshot, &entry)) {
            do {
                if (entry.th32ProcessID == GetCurrentProcessId()) {
                    parent = entry.th32ParentProcessID;
                    break;
                }
            } while (Process32NextW(snapshot, &entry));
        }
        CloseHandle(snapshot);
        if (!parent) return false;
        parent_handle = OpenProcess(SYNCHRONIZE, FALSE, parent);
        return parent_handle != nullptr;
#else
        return parent_pid > 0;
#endif
    }
    void watch_parent() {
        parent_timer.expires_after(std::chrono::milliseconds(250));
        parent_timer.async_wait([this](asio::error_code ec) {
            if (ec) return;
#ifdef _WIN32
            DWORD state = WaitForSingleObject(parent_handle, 0);
            if (state == WAIT_OBJECT_0) { stop(); return; }
            if (state != WAIT_TIMEOUT) {
                enqueue(J{{"protocol", 1}, {"event", "error"}, {"connection", ""},
                          {"error", error_value("parent_watch_failed", "parent process wait failed")}});
                stop(3);
                return;
            }
#else
            if (getppid() != parent_pid) { stop(); return; }
#endif
            watch_parent();
        });
    }
public:
    ~Server() {
#ifdef _WIN32
        if (parent_handle) CloseHandle(parent_handle);
#endif
    }
    int run() {
#ifndef _WIN32
        std::signal(SIGPIPE, SIG_IGN);
#endif
        if (!setup_parent_watch()) {
            std::cerr << J{{"protocol", 1}, {"error", error_value("parent_watch_failed", "cannot establish native parent process watcher")}}.dump() << '\n';
            return 3;
        }
        signals.async_wait([this](asio::error_code ec, int) { if (!ec) stop(); });
        lifetime.expires_after(std::chrono::seconds(3600));
        lifetime.async_wait([this](asio::error_code ec) { if (!ec) limit("lifetime_limit"); });
        reader = std::thread([this] { input_loop(); });
        writer = std::thread([this] { output_loop(); });
        watch(); watch_parent(); io.run(); stop();
        reader.join();
        // A stalled synchronous Windows pipe must not retain an orphan helper.
        std::atomic<bool> joined{false};
        std::thread deadline([&] {
            for (int i = 0; i < 60 && !joined; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(50));
            if (!joined) std::_Exit(3);
        });
#ifdef _WIN32
        if (pipe_failed) CancelSynchronousIo(writer.native_handle());
#endif
        writer.join();
        if (pipe_failed) {
            std::cerr << J{{"protocol", 1}, {"error", error_value("output_failed", "stdout stalled, closed, or bounded output queue exhausted")}}.dump() << '\n';
            joined = true; deadline.join();
            return 3;
        }
        joined = true; deadline.join();
        return exit_status;
    }
};

int run(const std::vector<std::string>& args) {
    auto invalid = [] {
        std::cerr << J{{"protocol", 1}, {"error", error_value("invalid_arguments", usage)}}.dump() << '\n';
        return 2;
    };
    try {
        for (auto& arg : args) J(arg).dump(-1, ' ', true);
        if (args == std::vector<std::string>{"serve"}) return Server().run();
        J result;
        if (args == std::vector<std::string>{"--version"}) result = J{{"protocol", 1}, {"version", "0.1.0"}};
        else if (args == std::vector<std::string>{"list"}) result = J{{"protocol", 1}, {"ports", ports()}};
        else if (args.size() == 3 && args[0] == "echo" && args[1] == "--text")
            result = J{{"protocol", 1}, {"text", args[2]}};
        else return invalid();
        std::cout << result.dump(-1, ' ', true) << '\n'; return 0;
    } catch (const std::exception&) { return invalid(); }
}
#ifdef _WIN32
int wmain(int argc, wchar_t* argv[]) {
    _setmode(_fileno(stdin), _O_BINARY); _setmode(_fileno(stdout), _O_BINARY); _setmode(_fileno(stderr), _O_BINARY);
    std::vector<std::string> args;
    for (int i = 1; i < argc; ++i) {
        int n = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, nullptr, 0, nullptr, nullptr);
        if (!n) return run({"invalid"});
        std::string s(n, '\0');
        WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, s.data(), n, nullptr, nullptr);
        s.pop_back(); args.push_back(std::move(s));
    }
    return run(args);
}
#else
int main(int argc, char* argv[]) { return run(std::vector<std::string>(argv + 1, argv + argc)); }
#endif
