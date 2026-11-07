import UIKit
import Capacitor
import PhotosUI
import UniformTypeIdentifiers

class SceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?

    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options connectionOptions: UIScene.ConnectionOptions) {
        guard let windowScene = scene as? UIWindowScene else { return }

        window = UIWindow(windowScene: windowScene)
        window?.rootViewController = AppBridgeViewController()
        window?.makeKeyAndVisible()

        SceneDelegateProxy.shared.scene(scene, willConnectTo: session, options: connectionOptions)
    }

    func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {
        SceneDelegateProxy.shared.scene(scene, openURLContexts: URLContexts)
    }

    func scene(_ scene: UIScene, continue userActivity: NSUserActivity) {
        SceneDelegateProxy.shared.scene(scene, continue: userActivity)
    }
}

/// アプリ専用のプラグインを登録する
class AppBridgeViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(VideoPickerPlugin())
    }
}

/// 写真ライブラリから動画を「変換せずに」受け取る。
/// ブラウザの選択画面は HEVC をブラウザ用の形式に変換するため長い試合だと数分かかるが、
/// PHPicker の .current 指定なら元のファイルがそのまま来る（iPhone 内にあればほぼ一瞬。iCloud のみの動画はダウンロードの時間だけ）。
@objc(VideoPickerPlugin)
public class VideoPickerPlugin: CAPPlugin, CAPBridgedPlugin, PHPickerViewControllerDelegate {
    public let identifier = "VideoPickerPlugin"
    public let jsName = "VideoPicker"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "pick", returnType: CAPPluginReturnPromise)
    ]
    private var pending: CAPPluginCall?

    @objc func pick(_ call: CAPPluginCall) {
        pending = call
        DispatchQueue.main.async {
            var config = PHPickerConfiguration()
            config.filter = .videos
            config.selectionLimit = 1
            config.preferredAssetRepresentationMode = .current   // 変換しない
            let picker = PHPickerViewController(configuration: config)
            picker.delegate = self
            self.bridge?.viewController?.present(picker, animated: true)
        }
    }

    public func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
        picker.dismiss(animated: true)
        guard let call = pending else { return }
        pending = nil
        guard let provider = results.first?.itemProvider else {
            call.resolve(["cancelled": true])
            return
        }
        notifyListeners("loading", data: [:])
        provider.loadFileRepresentation(forTypeIdentifier: UTType.movie.identifier) { url, error in
            guard let url = url else {
                call.reject("動画を読み込めませんでした: \(error?.localizedDescription ?? "")")
                return
            }
            // 渡された URL はこの中だけ有効なので、アプリの一時フォルダへ移す（同じ端末内のコピーなので速い）
            let fm = FileManager.default
            let dir = fm.temporaryDirectory.appendingPathComponent("picked", isDirectory: true)
            try? fm.removeItem(at: dir)
            try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
            // 拡張子は小文字に（Capacitor が .mov/.mp4 を動画として扱い、部分読み出し（Range）に応じるため）
            let name = url.deletingPathExtension().lastPathComponent + "." + url.pathExtension.lowercased()
            let dest = dir.appendingPathComponent(name)
            do {
                try fm.copyItem(at: url, to: dest)
                let size = (try? fm.attributesOfItem(atPath: dest.path)[.size] as? NSNumber)?.int64Value ?? 0
                call.resolve(["path": dest.absoluteString, "name": name, "size": size])
            } catch {
                call.reject("動画をコピーできませんでした: \(error.localizedDescription)")
            }
        }
    }
}
