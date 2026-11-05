#include <iostream>
#include <string>
#include <string_view>
#include <vector>
#include <utility>
#include <cstdio>

#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#endif

namespace {
constexpr std::string_view invalid_arguments =
    R"({"protocol":1,"error":{"code":"invalid_arguments","message":"expected greet --name <non-empty UTF-8 name> or --version"}})";

bool valid_utf8(std::string_view value) {
    for (std::size_t i = 0; i < value.size();) {
        const auto first = static_cast<unsigned char>(value[i++]);
        if (first <= 0x7f) {
            continue;
        }
        unsigned int remaining;
        unsigned int codepoint;
        unsigned int minimum;
        if (first >= 0xc2 && first <= 0xdf) {
            remaining = 1;
            codepoint = first & 0x1f;
            minimum = 0x80;
        } else if (first >= 0xe0 && first <= 0xef) {
            remaining = 2;
            codepoint = first & 0x0f;
            minimum = 0x800;
        } else if (first >= 0xf0 && first <= 0xf4) {
            remaining = 3;
            codepoint = first & 0x07;
            minimum = 0x10000;
        } else {
            return false;
        }
        if (remaining > value.size() - i) {
            return false;
        }
        while (remaining-- > 0) {
            const auto next = static_cast<unsigned char>(value[i++]);
            if ((next & 0xc0) != 0x80) {
                return false;
            }
            codepoint = (codepoint << 6) | (next & 0x3f);
        }
        // Reject overlong encodings, surrogate code points, and out-of-range values.
        if (codepoint < minimum || codepoint > 0x10ffff ||
            (codepoint >= 0xd800 && codepoint <= 0xdfff)) {
            return false;
        }
    }
    return true;
}

std::string quote(std::string_view value) {
    constexpr char hex[] = "0123456789abcdef";
    std::string result = "\"";
    for (unsigned char character : value) {
        switch (character) {
        case '"': result += "\\\""; break;
        case '\\': result += "\\\\"; break;
        case '\b': result += "\\b"; break;
        case '\f': result += "\\f"; break;
        case '\n': result += "\\n"; break;
        case '\r': result += "\\r"; break;
        case '\t': result += "\\t"; break;
        default:
            if (character < 0x20) {
                result += "\\u00";
                result += hex[character >> 4];
                result += hex[character & 0x0f];
            } else {
                result += static_cast<char>(character);
            }
        }
    }
    result += '"';
    return result;
}
} // namespace

int run(int argc, char* argv[]) {
    for (int i = 1; i < argc; ++i) {
        if (!valid_utf8(argv[i])) {
            std::cerr << invalid_arguments << '\n';
            return 2;
        }
    }
    if (argc == 2 && std::string_view(argv[1]) == "--version") {
        std::cout << R"({"protocol":1,"version":"0.1.0"})" << '\n';
        return 0;
    }
    if (argc == 4 && std::string_view(argv[1]) == "greet" &&
        std::string_view(argv[2]) == "--name" && argv[3][0] != '\0') {
        std::cout << R"({"protocol":1,"message":)"
                  << quote(std::string("Hello, ") + argv[3] + "!") << "}\n";
        return 0;
    }
    std::cerr << invalid_arguments << '\n';
    return 2;
}

#ifdef _WIN32
int wmain(int argc, wchar_t* argv[]) {
    if (_setmode(_fileno(stdout), _O_BINARY) == -1 ||
        _setmode(_fileno(stderr), _O_BINARY) == -1) {
        std::cerr << "failed to configure binary output streams\n";
        return 1;
    }
    std::vector<std::string> arguments;
    arguments.reserve(argc);
    for (int i = 0; i < argc; ++i) {
        const int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS,
            argv[i], -1, nullptr, 0, nullptr, nullptr);
        if (size == 0) {
            std::cerr << invalid_arguments << '\n';
            return 2;
        }
        std::string argument(size, '\0');
        if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS,
            argv[i], -1, argument.data(), size, nullptr, nullptr) != size) {
            std::cerr << invalid_arguments << '\n';
            return 2;
        }
        argument.pop_back();
        arguments.push_back(std::move(argument));
    }
    std::vector<char*> pointers;
    pointers.reserve(argc);
    for (auto& argument : arguments) {
        pointers.push_back(argument.data());
    }
    return run(argc, pointers.data());
}
#else
int main(int argc, char* argv[]) {
    return run(argc, argv);
}
#endif
