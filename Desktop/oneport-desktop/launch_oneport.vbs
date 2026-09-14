' OnePort launcher — opens the app window with no console flash.
' Self-healing: closes any stuck previous instance first, so the window always
' opens fresh (a lingering process can hold the WebView2 profile and block it).
Dim sh
Set sh = CreateObject("WScript.Shell")
On Error Resume Next
sh.Run "taskkill /F /IM OnePort.exe /T", 0, True   ' hidden, wait
On Error Goto 0
sh.CurrentDirectory = "C:\Users\Bitan\Desktop\oneport-desktop"
sh.Run """C:\Users\Bitan\Desktop\oneport-desktop\dist\OnePort\OnePort.exe""", 0, False
