#include <Windows.h>
#include <shobjidl.h>
#include <filesystem>
#include <string>

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int)
{
    wchar_t executable[32768] = {};
    if (!GetModuleFileNameW(nullptr, executable, 32768)) return 1;
    const auto bundle = std::filesystem::path(executable).parent_path();
    const auto runtime = bundle / L"Runtime" / L"Python";
    const auto library = runtime / L"python311.dll";
    auto script = (bundle / L"launch.py").wstring();
    if (!std::filesystem::exists(library) || !std::filesystem::exists(script))
    {
        MessageBoxW(nullptr, L"请保留完整的 LevelControl 客户端目录，不能单独移动 EXE。",
                    L"LevelControl 启动失败", MB_OK | MB_ICONERROR);
        return 1;
    }
    SetCurrentProcessExplicitAppUserModelID(L"LevelControl.Client");
    SetEnvironmentVariableW(L"PYTHONHOME", runtime.c_str());
    SetEnvironmentVariableW(L"PYTHONPATH", (bundle / L"Companion" / L"tools").c_str());
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");
    SetDllDirectoryW(runtime.c_str());
    HMODULE python = LoadLibraryExW(library.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!python)
    {
        MessageBoxW(nullptr, L"无法加载随客户端提供的 Python 运行时，请检查 Runtime 目录。",
                    L"LevelControl 启动失败", MB_OK | MB_ICONERROR);
        return 1;
    }
    using PythonMain = int (*)(int, wchar_t**);
    auto run = reinterpret_cast<PythonMain>(GetProcAddress(python, "Py_Main"));
    if (!run) return 1;
    wchar_t* arguments[] = {executable, script.data()};
    // Embed the UI in this process so Windows displays LevelControl.exe, not pythonw.exe.
    return run(2, arguments);
}
