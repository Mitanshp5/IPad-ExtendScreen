#import "PSSettingsViewController.h"
#import "PSScreenSettings.h"
#import "PSConnectionClient.h"
#import "PSKeychainStore.h"

@interface PSSettingsViewController () <UITextFieldDelegate>
@property (nonatomic, strong) UIScrollView *scroll;
@property (nonatomic, strong) UILabel *intro;
@property (nonatomic, strong) UILabel *modeTitle;
@property (nonatomic, strong) UISegmentedControl *modeControl;
@property (nonatomic, strong) UILabel *serverTitle;
@property (nonatomic, strong) UITextField *serverField;
@property (nonatomic, strong) UILabel *keyTitle;
@property (nonatomic, strong) UITextField *keyField;
@property (nonatomic, strong) UIButton *pairButton;
@property (nonatomic, strong) UIButton *testButton;
@property (nonatomic, strong) UIButton *forgetWiFiButton;
@property (nonatomic, strong) UILabel *cableHelp;
@property (nonatomic, strong) UILabel *fpsTitle;
@property (nonatomic, strong) UISegmentedControl *fpsControl;
@property (nonatomic, strong) UILabel *inputTitle;
@property (nonatomic, strong) UISwitch *inputSwitch;
@property (nonatomic, strong) UILabel *showFPSTitle;
@property (nonatomic, strong) UISwitch *showFPSSwitch;
@property (nonatomic, strong) UILabel *resultLabel;
@property (nonatomic, strong) PSConnectionClient *connectionClient;
@property (nonatomic) UIEdgeInsets savedContentInset;
@property (nonatomic) UIEdgeInsets savedIndicatorInset;
@property (nonatomic) BOOL keyboardInsetsSaved;
@end

@implementation PSSettingsViewController
- (UILabel *)label:(NSString *)text {
    UILabel *label = [UILabel new];
    label.text = text;
    label.textColor = [UIColor colorWithWhite:0.74 alpha:1];
    label.font = [UIFont systemFontOfSize:18];
    label.numberOfLines = 0;
    [self.scroll addSubview:label];
    return label;
}

- (UITextField *)field {
    UITextField *field = [UITextField new];
    field.textColor = [UIColor whiteColor];
    field.backgroundColor = [UIColor colorWithWhite:0.13 alpha:1];
    field.borderStyle = UITextBorderStyleRoundedRect;
    field.font = [UIFont systemFontOfSize:19];
    field.autocapitalizationType = UITextAutocapitalizationTypeNone;
    field.autocorrectionType = UITextAutocorrectionTypeNo;
    field.spellCheckingType = UITextSpellCheckingTypeNo;
    field.clearButtonMode = UITextFieldViewModeWhileEditing;
    field.delegate = self;
    [self.scroll addSubview:field];
    return field;
}

- (UIButton *)actionButton:(NSString *)title {
    UIButton *button = [UIButton buttonWithType:UIButtonTypeSystem];
    [button setTitle:title forState:UIControlStateNormal];
    button.titleLabel.font = [UIFont systemFontOfSize:21];
    [self.scroll addSubview:button];
    return button;
}

- (UIButton *)dangerButton:(NSString *)title {
    UIButton *button = [self actionButton:title];
    [button setTitleColor:[UIColor colorWithRed:0.95 green:0.61 blue:0.56 alpha:1]
                 forState:UIControlStateNormal];
    button.titleLabel.font = [UIFont systemFontOfSize:18];
    return button;
}

- (id)storedObjectForKey:(NSString *)key bundled:(NSDictionary *)bundled {
    id stored = [[NSUserDefaults standardUserDefaults] objectForKey:key];
    return stored ?: bundled[key];
}

- (void)viewDidLoad {
    [super viewDidLoad];
    self.title = @"Extend Screen";
    self.view.backgroundColor = [UIColor colorWithRed:0.035 green:0.055 blue:0.075 alpha:1];
    self.navigationItem.rightBarButtonItem = [[UIBarButtonItem alloc]
        initWithTitle:@"Save" style:UIBarButtonItemStyleDone target:self action:@selector(save)];
    self.navigationItem.leftBarButtonItem = [[UIBarButtonItem alloc]
        initWithTitle:@"Back" style:UIBarButtonItemStylePlain target:self action:@selector(goBack)];

    self.scroll = [[UIScrollView alloc] initWithFrame:self.view.bounds];
    self.scroll.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    self.scroll.keyboardDismissMode = UIScrollViewKeyboardDismissModeOnDrag;
    [self.view addSubview:self.scroll];

    NSDictionary *bundled = [NSDictionary dictionaryWithContentsOfFile:
        [[NSBundle mainBundle] pathForResource:@"ScreenDefaults" ofType:@"plist"]] ?: @{};

    self.intro = [self label:@"Wi-Fi: unencrypted HTTP on trusted Wi-Fi only. USB cable: plug in the cable; no password or code is needed. Nothing is sent to the internet."];
    self.modeTitle = [self label:@"Connection"];
    self.modeControl = [[UISegmentedControl alloc] initWithItems:@[@"Wi-Fi", @"USB cable"]];
    self.modeControl.selectedSegmentIndex = [[self storedObjectForKey:PSModeKey bundled:bundled] integerValue] == PSModeCable ? 1 : 0;
    if (self.modeControl.selectedSegmentIndex < 0) self.modeControl.selectedSegmentIndex = 0;
    [self.modeControl addTarget:self action:@selector(refreshModeAppearance)
               forControlEvents:UIControlEventValueChanged];
    [self.scroll addSubview:self.modeControl];

    self.serverTitle = [self label:@"PC host origin (Wi-Fi)"];
    self.serverField = [self field];
    self.serverField.keyboardType = UIKeyboardTypeURL;
    [self.serverField addTarget:self action:@selector(refreshModeAppearance)
               forControlEvents:UIControlEventEditingChanged];
    NSString *savedURL = [[NSUserDefaults standardUserDefaults] stringForKey:PSServerURLKey];
    self.serverField.text = savedURL.length ? savedURL : bundled[PSServerURLKey];
    self.serverField.placeholder = @"http://192.168.1.100:8644";
    self.serverField.accessibilityLabel = @"PC host origin";

    self.keyTitle = [self label:@"Eight-digit Wi-Fi pairing code from the PC"];
    self.keyField = [self field];
    self.keyField.secureTextEntry = YES;
    self.keyField.keyboardType = UIKeyboardTypeNumberPad;
    self.keyField.placeholder = @"Enter the code printed by screen-host.py";
    self.keyField.accessibilityLabel = @"Wi-Fi pairing code. Secure entry.";

    self.pairButton = [self actionButton:@"Pair over Wi-Fi"];
    [self.pairButton addTarget:self action:@selector(pairDevice) forControlEvents:UIControlEventTouchUpInside];
    self.testButton = [self actionButton:@"Test Wi-Fi connection"];
    [self.testButton addTarget:self action:@selector(testConnection) forControlEvents:UIControlEventTouchUpInside];
    self.forgetWiFiButton = [self dangerButton:@"Forget Wi-Fi pairing"];
    [self.forgetWiFiButton addTarget:self action:@selector(confirmForgetWiFi) forControlEvents:UIControlEventTouchUpInside];

    self.cableHelp = [self label:@"USB cable mode needs no password. Connect the cable, then run start-cable-host.cmd on the PC. The PC reaches the app through Apple usbmuxd (Apple Mobile Device Support or libimobiledevice). The listener stays on 127.0.0.1:8765 inside the iPad."];

    self.fpsTitle = [self label:@"Stream rate"];
    self.fpsControl = [[UISegmentedControl alloc] initWithItems:@[@"15 fps", @"30 fps", @"45 fps", @"60 fps"]];
    NSInteger fps = [[self storedObjectForKey:PSFPSKey bundled:bundled] integerValue];
    self.fpsControl.selectedSegmentIndex = fps == 15 ? 0 : (fps == 45 ? 2 : (fps == 60 ? 3 : 1));
    [self.scroll addSubview:self.fpsControl];

    self.inputTitle = [self label:@"Forward touch and keyboard to the PC"];
    self.inputSwitch = [UISwitch new];
    id input = [self storedObjectForKey:PSInputKey bundled:bundled];
    self.inputSwitch.on = input ? [input boolValue] : YES;
    [self.scroll addSubview:self.inputSwitch];

    self.showFPSTitle = [self label:@"Show frame rate"];
    self.showFPSSwitch = [UISwitch new];
    id showFPS = [self storedObjectForKey:PSShowFPSKey bundled:bundled];
    self.showFPSSwitch.on = showFPS ? [showFPS boolValue] : NO;
    [self.scroll addSubview:self.showFPSSwitch];

    self.resultLabel = [self label:@"Wi-Fi credentials stay on this iPad in the Keychain. USB cable mode uses the physical cable only and needs no password."];
    [self refreshModeAppearance];

    NSNotificationCenter *center = [NSNotificationCenter defaultCenter];
    [center addObserver:self selector:@selector(appResigningActive) name:UIApplicationWillResignActiveNotification object:nil];
    [center addObserver:self selector:@selector(keyboardChanged:) name:UIKeyboardWillChangeFrameNotification object:nil];
    [center addObserver:self selector:@selector(keyboardHidden:) name:UIKeyboardWillHideNotification object:nil];
}

- (void)viewWillAppear:(BOOL)animated {
    [super viewWillAppear:animated];
    self.navigationController.navigationBarHidden = NO;
}

- (void)goBack {
    [self.navigationController popViewControllerAnimated:YES];
}

- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    CGFloat width = MIN(self.view.bounds.size.width - 48, 680);
    CGFloat left = (self.view.bounds.size.width - width) / 2;
    self.intro.frame = CGRectMake(left, 20, width, 72);
    self.modeTitle.frame = CGRectMake(left, 104, width, 26);
    self.modeControl.frame = CGRectMake(left, 132, width, 36);
    self.serverTitle.frame = CGRectMake(left, 184, width, 26);
    self.serverField.frame = CGRectMake(left, 212, width, 48);
    self.keyTitle.frame = CGRectMake(left, 276, width, 26);
    self.keyField.frame = CGRectMake(left, 304, width, 48);
    self.pairButton.frame = CGRectMake(left, 364, width, 44);
    self.testButton.frame = CGRectMake(left, 414, width, 44);
    self.resultLabel.frame = CGRectMake(left, 468, width, 82);
    self.forgetWiFiButton.frame = CGRectMake(left, 558, width, 40);
    self.cableHelp.frame = CGRectMake(left, 610, width, 110);
    self.fpsTitle.frame = CGRectMake(left, 736, width, 26);
    self.fpsControl.frame = CGRectMake(left, 764, width, 36);
    self.inputTitle.frame = CGRectMake(left, 820, width - 70, 40);
    self.inputSwitch.frame = CGRectMake(CGRectGetMaxX(self.inputTitle.frame) + 12, 824, 58, 32);
    self.showFPSTitle.frame = CGRectMake(left, 874, width - 70, 40);
    self.showFPSSwitch.frame = CGRectMake(CGRectGetMaxX(self.showFPSTitle.frame) + 12, 878, 58, 32);
    self.scroll.contentSize = CGSizeMake(self.view.bounds.size.width, 930);
}

- (void)refreshModeAppearance {
    BOOL cable = self.modeControl.selectedSegmentIndex == 1;
    self.serverTitle.alpha = cable ? 0.45 : 1;
    self.serverField.enabled = !cable;
    self.keyTitle.alpha = cable ? 0.45 : 1;
    self.keyField.enabled = !cable;
    self.pairButton.enabled = !cable;
    self.testButton.enabled = !cable;
    self.forgetWiFiButton.enabled = !cable;
    self.cableHelp.alpha = cable ? 1 : 0.75;
}

- (void)save {
    [self.view endEditing:YES];
    NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
    NSString *server = [self.serverField.text stringByTrimmingCharactersInSet:
        [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    NSInteger mode = self.modeControl.selectedSegmentIndex == 1 ? PSModeCable : PSModeWiFi;

    if (mode == PSModeWiFi && server.length) {
        NSURLComponents *parts = [NSURLComponents componentsWithString:server];
        NSString *scheme = parts.scheme.lowercaseString;
        if (!parts.host.length || !([scheme isEqualToString:@"http"] || [scheme isEqualToString:@"https"]) ||
            parts.user.length || parts.password.length || parts.query.length || parts.fragment.length ||
            (parts.path.length && ![parts.path isEqualToString:@"/"])) {
            self.resultLabel.text = @"Use the host origin only, such as http://192.168.1.100:8644, without a path, credentials, query or fragment.";
            return;
        }
    }

    [defaults setObject:server ?: @"" forKey:PSServerURLKey];
    [defaults setInteger:mode forKey:PSModeKey];
    NSInteger fps = self.fpsControl.selectedSegmentIndex == 0 ? 15 :
                    (self.fpsControl.selectedSegmentIndex == 2 ? 45 :
                    (self.fpsControl.selectedSegmentIndex == 3 ? 60 : 30));
    [defaults setInteger:fps forKey:PSFPSKey];
    [defaults setBool:self.inputSwitch.on forKey:PSInputKey];
    [defaults setBool:self.showFPSSwitch.on forKey:PSShowFPSKey];
    self.keyField.text = @"";
    self.resultLabel.text = mode == PSModeCable ?
        @"Saved. Return to the screen; the app is now waiting for the USB cable host." :
        @"Saved. Return to the screen to connect over Wi-Fi.";
}

- (void)showResult:(NSString *)message alertTitle:(NSString *)title {
    self.resultLabel.text = message ?: @"";
    [self.scroll scrollRectToVisible:CGRectInset(self.resultLabel.frame, 0, -16) animated:YES];
    if (!title.length || !self.view.window) return;
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:title
        message:message preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:nil]];
    [self presentViewController:alert animated:YES completion:nil];
}

- (void)pairDevice {
    if (self.modeControl.selectedSegmentIndex == 1) {
        [self showResult:@"USB cable mode needs no pairing code. Plug in the cable and run start-cable-host.cmd on the PC."
             alertTitle:nil];
        return;
    }
    if (self.connectionClient) return;
    [self.view endEditing:YES];
    NSString *code = [self.keyField.text stringByTrimmingCharactersInSet:
        [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    NSString *server = [self.serverField.text stringByTrimmingCharactersInSet:
        [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (code.length != 8) {
        [self showResult:@"Enter the eight-digit code printed by screen-host.py on the PC."
             alertTitle:@"Pairing Code Needed"];
        return;
    }
    self.keyField.enabled = NO;
    self.pairButton.enabled = NO;
    [self.pairButton setTitle:@"Pairing..." forState:UIControlStateNormal];
    [self showResult:@"Pairing this iPad over Wi-Fi..." alertTitle:nil];
    self.connectionClient = [PSConnectionClient new];
    __weak PSSettingsViewController *weakSelf = self;
    [self.connectionClient pairWithCode:code serverURL:server completion:^(BOOL paired, NSString *message) {
        PSSettingsViewController *controller = weakSelf;
        if (!controller) return;
        controller.keyField.enabled = YES;
        controller.pairButton.enabled = YES;
        [controller.pairButton setTitle:@"Pair over Wi-Fi" forState:UIControlStateNormal];
        controller.connectionClient = nil;
        if (paired) {
            [[NSUserDefaults standardUserDefaults] setObject:server forKey:PSServerURLKey];
            [[NSUserDefaults standardUserDefaults] setInteger:PSModeWiFi forKey:PSModeKey];
            controller.keyField.text = @"";
        }
        [controller showResult:message alertTitle:paired ? @"Wi-Fi Paired" : @"Wi-Fi Pairing Failed"];
    }];
}

- (void)testConnection {
    if (self.modeControl.selectedSegmentIndex == 1) {
        [self showResult:@"Cable mode has no HTTP health check. Plug in the USB cable, run start-cable-host.cmd on the PC, then return to the screen."
             alertTitle:nil];
        return;
    }
    if (self.connectionClient) {
        [self.connectionClient cancel];
        [self.testButton setTitle:@"Test Wi-Fi connection" forState:UIControlStateNormal];
        return;
    }
    [self.view endEditing:YES];
    NSString *server = [self.serverField.text stringByTrimmingCharactersInSet:
        [NSCharacterSet whitespaceAndNewlineCharacterSet]];
    [self showResult:@"Testing the Wi-Fi host. No screen data or credential is sent." alertTitle:nil];
    [self.testButton setTitle:@"Cancel connection test" forState:UIControlStateNormal];
    self.connectionClient = [PSConnectionClient new];
    __weak PSSettingsViewController *weakSelf = self;
    [self.connectionClient testServerURL:server completion:^(BOOL reachable, NSString *message) {
        PSSettingsViewController *controller = weakSelf;
        if (!controller) return;
        [controller.testButton setTitle:@"Test Wi-Fi connection" forState:UIControlStateNormal];
        controller.connectionClient = nil;
        if (reachable) [[NSUserDefaults standardUserDefaults] setObject:server forKey:PSServerURLKey];
        [controller showResult:message alertTitle:reachable ? @"Wi-Fi Host Reachable" : @"Wi-Fi Test Failed"];
    }];
}

- (void)restoreKeyboardInsets {
    if (!self.keyboardInsetsSaved) return;
    self.scroll.contentInset = self.savedContentInset;
    self.scroll.scrollIndicatorInsets = self.savedIndicatorInset;
    self.keyboardInsetsSaved = NO;
}

- (void)keyboardHidden:(NSNotification *)notification { [self restoreKeyboardInsets]; }

- (void)keyboardChanged:(NSNotification *)notification {
    if (!self.view.window || self.navigationController.topViewController != self) return;
    if (!self.keyboardInsetsSaved) {
        self.savedContentInset = self.scroll.contentInset;
        self.savedIndicatorInset = self.scroll.scrollIndicatorInsets;
        self.keyboardInsetsSaved = YES;
    }
    CGRect screen = [notification.userInfo[UIKeyboardFrameEndUserInfoKey] CGRectValue];
    CGRect local = [self.scroll convertRect:screen fromView:nil];
    CGRect overlap = CGRectIntersection(self.scroll.bounds, local);
    BOOL bottom = !CGRectIsNull(overlap) && CGRectGetMaxY(local) >= CGRectGetMaxY(self.scroll.bounds) - 1;
    CGFloat occlusion = bottom && overlap.size.width >= self.scroll.bounds.size.width * 0.8 ? overlap.size.height : 0;
    UIEdgeInsets content = self.savedContentInset, indicator = self.savedIndicatorInset;
    content.bottom += occlusion;
    indicator.bottom += occlusion;
    self.scroll.contentInset = content;
    self.scroll.scrollIndicatorInsets = indicator;
    UITextField *active = self.keyField.isFirstResponder ? self.keyField :
        (self.serverField.isFirstResponder ? self.serverField : nil);
    if (active) [self.scroll scrollRectToVisible:CGRectInset(active.frame, 0, -120) animated:YES];
}

- (void)appResigningActive {
    [self.view endEditing:YES];
    [self restoreKeyboardInsets];
    [self.connectionClient cancel];
    self.keyField.text = @"";
}

- (void)confirmForgetWiFi {
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:@"Forget the Wi-Fi pairing?"
        message:@"Only this iPad's saved Wi-Fi credential is removed. The PC's device list is unchanged."
        preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"Cancel" style:UIAlertActionStyleCancel handler:nil]];
    __weak PSSettingsViewController *weakSelf = self;
    [alert addAction:[UIAlertAction actionWithTitle:@"Forget" style:UIAlertActionStyleDestructive handler:^(UIAlertAction *action) {
        PSSettingsViewController *controller = weakSelf;
        if (!controller) return;
        [controller.connectionClient cancel];
        BOOL ok = [PSKeychainStore removeDeviceToken];
        controller.resultLabel.text = ok ? @"Wi-Fi pairing removed." : @"Wi-Fi credential removal failed.";
    }]];
    [self presentViewController:alert animated:YES completion:nil];
}

- (BOOL)textFieldShouldReturn:(UITextField *)field {
    [field resignFirstResponder];
    return YES;
}

- (void)viewWillDisappear:(BOOL)animated {
    [self.view endEditing:YES];
    [self restoreKeyboardInsets];
    [self.connectionClient cancel];
    self.keyField.text = @"";
    [super viewWillDisappear:animated];
}

- (void)dealloc {
    [[NSNotificationCenter defaultCenter] removeObserver:self];
}
@end
