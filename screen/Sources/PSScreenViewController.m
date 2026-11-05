#import "PSScreenViewController.h"
#import "PSSettingsViewController.h"
#import "PSKeyboardViewController.h"
#import "PSStreamClient.h"
#import "PSInputClient.h"
#import "PSCableServer.h"
#import "PSKeychainStore.h"
#import "PSScreenSettings.h"
#import <ImageIO/ImageIO.h>
#import <QuartzCore/QuartzCore.h>

static const NSUInteger PSDefaultStreamWidth = 1024;
static const NSUInteger PSDefaultStreamHeight = 768;
static const NSTimeInterval PSChromeIdleDelay = 4.5;
static const CGFloat PSChromeHotZoneWidth = 120.0;
static const CGFloat PSChromeHotZoneHeight = 140.0;

@interface PSScreenViewController () <UIGestureRecognizerDelegate, PSCableServerDelegate>
@property (nonatomic, strong) UIImageView *imageView;
@property (nonatomic, strong) UILabel *statusLabel;
@property (nonatomic, strong) UIButton *settingsButton;
@property (nonatomic, strong) UIButton *keyboardButton;
@property (nonatomic, strong) UILabel *fpsLabel;
@property (nonatomic, strong) PSStreamClient *stream;
@property (nonatomic, strong) PSInputClient *input;
@property (nonatomic, strong) PSCableServer *cableServer;
@property (nonatomic, strong) NSTimer *reconnectTimer;
@property (nonatomic, strong) NSTimer *fpsTimer;
@property (nonatomic, strong) NSTimer *chromeTimer;
@property (nonatomic) BOOL chromeHidden;
@property (nonatomic, strong) NSString *lastSignature;
@property (nonatomic, copy) NSString *serverURL;
@property (nonatomic, copy) NSString *token;
@property (nonatomic) NSUInteger streamFPS;
@property (nonatomic) BOOL inputEnabled;
@property (nonatomic) BOOL cableMode;
@property (nonatomic) BOOL viewVisible;
@property (nonatomic) BOOL suspendForSettings;
@property (nonatomic) BOOL streaming;
@property (nonatomic) BOOL connecting;
@property (nonatomic) BOOL acceptFrames;
@property (nonatomic) BOOL decoding;
@property (nonatomic) BOOL dragging;
@property (nonatomic, strong) NSData *pendingFrame;
@property (nonatomic) NSUInteger reconnectAttempt;
@property (nonatomic) NSUInteger framesThisSecond;
@property (nonatomic) NSTimeInterval lastMoveSent;
@property (nonatomic) dispatch_queue_t decodeQueue;
@end

@implementation PSScreenViewController
- (instancetype)init {
    self = [super init];
    if (self) {
        _decodeQueue = dispatch_queue_create("local.extendscreen.decode", DISPATCH_QUEUE_SERIAL);
    }
    return self;
}

- (PSCableServer *)cableServer {
    if (!_cableServer) {
        _cableServer = [PSCableServer new];
        _cableServer.delegate = self;
    }
    return _cableServer;
}

- (UIButton *)chromeButton:(NSString *)title label:(NSString *)label {
    UIButton *button = [UIButton buttonWithType:UIButtonTypeSystem];
    [button setTitle:title forState:UIControlStateNormal];
    [button setTitleColor:[UIColor colorWithWhite:0.96 alpha:1] forState:UIControlStateNormal];
    button.titleLabel.font = [UIFont systemFontOfSize:23 weight:UIFontWeightRegular];
    button.backgroundColor = [UIColor colorWithWhite:0.10 alpha:0.82];
    button.layer.cornerRadius = 10;
    button.accessibilityLabel = label;
    [self.view addSubview:button];
    return button;
}

- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = [UIColor blackColor];
    self.view.multipleTouchEnabled = YES;
    self.imageView = [[UIImageView alloc] initWithFrame:self.view.bounds];
    self.imageView.contentMode = UIViewContentModeScaleAspectFit;
    self.imageView.backgroundColor = [UIColor blackColor];
    self.imageView.userInteractionEnabled = NO;
    self.imageView.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    [self.view addSubview:self.imageView];

    self.statusLabel = [UILabel new];
    self.statusLabel.textColor = [UIColor colorWithWhite:0.95 alpha:1];
    self.statusLabel.font = [UIFont systemFontOfSize:18];
    self.statusLabel.textAlignment = NSTextAlignmentCenter;
    self.statusLabel.numberOfLines = 0;
    self.statusLabel.backgroundColor = [UIColor colorWithWhite:0.08 alpha:0.86];
    self.statusLabel.layer.cornerRadius = 10;
    self.statusLabel.clipsToBounds = YES;
    self.statusLabel.hidden = YES;
    [self.view addSubview:self.statusLabel];

    self.settingsButton = [self chromeButton:@"\u2699" label:@"Settings"];
    [self.settingsButton addTarget:self action:@selector(openSettings) forControlEvents:UIControlEventTouchUpInside];
    self.keyboardButton = [self chromeButton:@"\u2328" label:@"PC keyboard"];
    [self.keyboardButton addTarget:self action:@selector(openKeyboard) forControlEvents:UIControlEventTouchUpInside];

    self.fpsLabel = [UILabel new];
    self.fpsLabel.textColor = [UIColor colorWithWhite:0.62 alpha:1];
    self.fpsLabel.backgroundColor = [UIColor colorWithWhite:0 alpha:0.45];
    self.fpsLabel.font = [UIFont monospacedDigitSystemFontOfSize:13 weight:UIFontWeightRegular];
    self.fpsLabel.textAlignment = NSTextAlignmentCenter;
    self.fpsLabel.hidden = YES;
    [self.view addSubview:self.fpsLabel];

    UITapGestureRecognizer *tap = [[UITapGestureRecognizer alloc] initWithTarget:self action:@selector(handleTap:)];
    tap.numberOfTouchesRequired = 1;
    tap.delegate = self;
    [self.view addGestureRecognizer:tap];

    UIPanGestureRecognizer *drag = [[UIPanGestureRecognizer alloc] initWithTarget:self action:@selector(handleDrag:)];
    drag.minimumNumberOfTouches = 1;
    drag.maximumNumberOfTouches = 1;
    drag.delegate = self;
    [self.view addGestureRecognizer:drag];

    UIPanGestureRecognizer *scroll = [[UIPanGestureRecognizer alloc] initWithTarget:self action:@selector(handleScroll:)];
    scroll.minimumNumberOfTouches = 2;
    scroll.maximumNumberOfTouches = 2;
    scroll.delegate = self;
    [self.view addGestureRecognizer:scroll];

    UITapGestureRecognizer *rightTap = [[UITapGestureRecognizer alloc] initWithTarget:self action:@selector(handleRightTap:)];
    rightTap.numberOfTouchesRequired = 2;
    rightTap.delegate = self;
    [self.view addGestureRecognizer:rightTap];

    [tap requireGestureRecognizerToFail:rightTap];
    [drag requireGestureRecognizerToFail:scroll];

    self.input = [PSInputClient new];
    __weak PSScreenViewController *weakSelf = self;
    self.input.failureHandler = ^(NSString *message) {
        PSScreenViewController *controller = weakSelf;
        if (controller) [controller setStatus:message];
    };

    NSNotificationCenter *center = [NSNotificationCenter defaultCenter];
    [center addObserver:self selector:@selector(appResignedActive) name:UIApplicationWillResignActiveNotification object:nil];
    [center addObserver:self selector:@selector(appBackgrounded) name:UIApplicationDidEnterBackgroundNotification object:nil];
    [center addObserver:self selector:@selector(appActivated) name:UIApplicationDidBecomeActiveNotification object:nil];
}

- (void)viewWillAppear:(BOOL)animated {
    [super viewWillAppear:animated];
    self.navigationController.navigationBarHidden = YES;
    }

- (void)viewDidAppear:(BOOL)animated {
    [super viewDidAppear:animated];
    self.viewVisible = YES;
    self.suspendForSettings = NO;
    [self startFPSTimer];
    [self showChrome];
    [self activateIfNeeded];
}

- (void)viewWillDisappear:(BOOL)animated {
    [self stopFPSTimer];
    [self.chromeTimer invalidate];
    self.chromeTimer = nil;
    self.viewVisible = NO;
        if (self.suspendForSettings) [self stopEverything];
    [super viewWillDisappear:animated];
}

- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    CGFloat width = self.view.bounds.size.width;
    CGFloat height = self.view.bounds.size.height;
    self.imageView.frame = self.view.bounds;
    CGFloat button = 44;
    CGFloat margin = 16;
    self.settingsButton.frame = CGRectMake(width - button - margin, margin, button, button);
    self.keyboardButton.frame = CGRectMake(width - button - margin, margin + button + 10, button, button);
    self.fpsLabel.frame = CGRectMake(margin, height - 30, 92, 21);
    if (!self.statusLabel.hidden) {
        CGFloat maxWidth = MIN(680, MAX(240, width - 120));
        CGSize fitted = [self.statusLabel sizeThatFits:CGSizeMake(maxWidth - 32, 420)];
        CGFloat labelWidth = MIN(maxWidth, MAX(180, fitted.width + 32));
        CGFloat labelHeight = MAX(48, fitted.height + 24);
        self.statusLabel.frame = CGRectMake((width - labelWidth) / 2,
                                            (height - labelHeight) / 2,
                                            labelWidth, labelHeight);
    }
}

- (void)dealloc {
    [[NSNotificationCenter defaultCenter] removeObserver:self];
    [self.reconnectTimer invalidate];
    [self.fpsTimer invalidate];
    [self.chromeTimer invalidate];
    [self.stream stop];
    [self.input cancel];
}

- (BOOL)prefersStatusBarHidden { return self.chromeHidden; }
- (UIStatusBarAnimation)preferredStatusBarUpdateAnimation { return UIStatusBarAnimationFade; }
- (BOOL)shouldAutorotate { return YES; }
- (UIInterfaceOrientationMask)supportedInterfaceOrientations {
    return UIInterfaceOrientationMaskAll;
}

- (void)setStatus:(NSString *)message {
    if (![NSThread isMainThread]) {
        dispatch_async(dispatch_get_main_queue(), ^{ [self setStatus:message]; });
        return;
    }
    self.statusLabel.text = message ?: @"";
    self.statusLabel.hidden = self.statusLabel.text.length == 0;
    [self.view setNeedsLayout];
}

- (void)startFPSTimer {
    if (self.fpsTimer) return;
    self.fpsTimer = [NSTimer scheduledTimerWithTimeInterval:1 target:self
        selector:@selector(fpsTick) userInfo:nil repeats:YES];
}

- (void)stopFPSTimer {
    [self.fpsTimer invalidate];
    self.fpsTimer = nil;
    self.framesThisSecond = 0;
}

- (void)fpsTick {
    if (self.fpsLabel.hidden) { self.framesThisSecond = 0; return; }
    self.fpsLabel.text = [NSString stringWithFormat:@"%lu fps", (unsigned long)self.framesThisSecond];
    self.framesThisSecond = 0;
}

- (void)activateIfNeeded {
    if (!self.viewVisible || !self.view.window) return;
    NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
    NSString *server = [defaults stringForKey:PSServerURLKey];
    NSInteger mode = [defaults integerForKey:PSModeKey] == PSModeCable ? PSModeCable : PSModeWiFi;
    NSInteger fps = [defaults integerForKey:PSFPSKey];
    if (fps < 5 || fps > 15) fps = 12;
    BOOL inputEnabled = [defaults boolForKey:PSInputKey];
    BOOL showFPS = [defaults boolForKey:PSShowFPSKey];
    self.fpsLabel.hidden = !showFPS;
    OSStatus status = errSecItemNotFound;
    NSString *token = [PSKeychainStore deviceTokenWithStatus:&status];
    NSString *signature = [NSString stringWithFormat:@"%ld|%@|%ld|%d|%lx",
        (long)mode, server ?: @"", (long)fps, inputEnabled,
        (unsigned long)token.hash];
    if ((self.streaming || self.connecting) && [signature isEqualToString:self.lastSignature]) return;
    if (self.reconnectTimer && [signature isEqualToString:self.lastSignature]) return;
    [self stopEverything];
    self.lastSignature = signature;
    self.serverURL = server;
    self.token = token;
    self.streamFPS = (NSUInteger)fps;
    self.inputEnabled = inputEnabled;

    if (mode == PSModeCable) {
        self.cableMode = YES;
        self.acceptFrames = YES;
        self.connecting = YES;
        self.cableServer.delegate = self;
        [self setStatus:@"Starting the USB cable listener..."];
        if (![self.cableServer startOnPort:PSCableListenPort]) {
            self.connecting = NO;
            [self setStatus:@"Could not start the USB cable listener on 127.0.0.1."];
        }
        return;
    }

    self.cableMode = NO;
    if (!server.length || status != errSecSuccess || !token.length) {
        [self setStatus:@"Open Settings to pair this iPad with the PC host."];
        return;
    }
    [self.input configureWithServerURL:server token:token enabled:inputEnabled];
    [self startStream];
}

- (void)startStream {
    if (!self.serverURL.length || !self.token.length) return;
    if (!self.stream) {
        self.stream = [PSStreamClient new];
        __weak PSScreenViewController *weakSelf = self;
        self.stream.frameHandler = ^(NSData *jpeg, NSUInteger sequence) {
            PSScreenViewController *controller = weakSelf;
            if (controller) [controller receiveFrame:jpeg];
        };
        self.stream.statusHandler = ^(BOOL streaming, NSString *message, NSInteger httpStatus) {
            PSScreenViewController *controller = weakSelf;
            if (!controller) return;
            [controller streamStatus:streaming message:message httpStatus:httpStatus];
        };
    }
    self.acceptFrames = YES;
    self.connecting = YES;
    self.streaming = NO;
    self.reconnectAttempt = 0;
    [self setStatus:@"Connecting to PC..."];
    [self.stream startWithServerURL:self.serverURL token:self.token fps:self.streamFPS];
}

- (void)streamStatus:(BOOL)streaming message:(NSString *)message httpStatus:(NSInteger)httpStatus {
    if (![NSThread isMainThread]) {
        dispatch_async(dispatch_get_main_queue(), ^{
            [self streamStatus:streaming message:message httpStatus:httpStatus];
        });
        return;
    }
    self.connecting = NO;
    if (streaming) {
        self.streaming = YES;
        self.reconnectAttempt = 0;
        [self cancelReconnect];
        [self setStatus:@""];
        [UIApplication sharedApplication].idleTimerDisabled = YES;
        return;
    }
    self.streaming = NO;
    [UIApplication sharedApplication].idleTimerDisabled = NO;
    if (httpStatus == 401 || httpStatus == 403) {
        [self setStatus:@"The PC rejected this iPad's pairing. Open Settings and pair again."];
        return;
    }
    [self setStatus:message.length ? message : @"Screen disconnected. Reconnecting..."];
    if (self.viewVisible && self.view.window) [self scheduleReconnect];
}

- (void)cableServer:(PSCableServer *)server status:(NSString *)status streaming:(BOOL)streaming {
    if (![NSThread isMainThread]) {
        dispatch_async(dispatch_get_main_queue(), ^{
            [self cableServer:server status:status streaming:streaming];
        });
        return;
    }
    if (!self.cableMode) return;
    self.connecting = YES;
    if (streaming) {
        self.streaming = YES;
        [self setStatus:@""];
        [UIApplication sharedApplication].idleTimerDisabled = YES;
        return;
    }
    self.streaming = NO;
    [UIApplication sharedApplication].idleTimerDisabled = NO;
    [self setStatus:status.length ? status : @"Waiting for the USB cable host..."];
}

- (void)cableServer:(PSCableServer *)server frame:(NSData *)jpeg {
    if (!self.cableMode || !self.acceptFrames) {
        return;
    }
    [self receiveFrame:jpeg];
}

- (void)scheduleReconnect {
    [self cancelReconnect];
    NSTimeInterval delay = MIN(8.0, 2.0 + (NSTimeInterval)self.reconnectAttempt);
    self.reconnectAttempt++;
    self.reconnectTimer = [NSTimer scheduledTimerWithTimeInterval:delay target:self
        selector:@selector(reconnectFired:) userInfo:nil repeats:NO];
}

- (void)reconnectFired:(NSTimer *)timer {
    self.reconnectTimer = nil;
    if (self.viewVisible && self.view.window) [self startStream];
}

- (void)cancelReconnect {
    [self.reconnectTimer invalidate];
    self.reconnectTimer = nil;
}

- (void)stopEverything {
    self.acceptFrames = NO;
    self.streaming = NO;
    self.connecting = NO;
    self.cableMode = NO;
    self.pendingFrame = nil;
    self.decoding = NO;
    [self cancelReconnect];
    [self.stream stop];
    [self.input cancel];
    [self.cableServer stop];
    [UIApplication sharedApplication].idleTimerDisabled = NO;
}

- (void)receiveFrame:(NSData *)jpeg {
    if (!self.acceptFrames || !jpeg.length) return;
    if (self.decoding) {
        self.pendingFrame = jpeg;
        return;
    }
    self.decoding = YES;
    __weak PSScreenViewController *weakSelf = self;
    dispatch_async(self.decodeQueue, ^{
        @autoreleasepool {
            UIImage *image = nil;
            CGImageSourceRef source = CGImageSourceCreateWithData((__bridge CFDataRef)jpeg,
                (__bridge CFDictionaryRef)@{(__bridge id)kCGImageSourceShouldCache: @NO});
            if (source) {
                CGImageRef cgImage = CGImageSourceCreateImageAtIndex(source, 0, NULL);
                if (cgImage) {
                    image = [UIImage imageWithCGImage:cgImage];
                    CGImageRelease(cgImage);
                }
                CFRelease(source);
            }
            dispatch_async(dispatch_get_main_queue(), ^{
                PSScreenViewController *controller = weakSelf;
                if (!controller) return;
                if (controller.acceptFrames && image) {
                    controller.imageView.image = image;
                    controller.framesThisSecond++;
                }
                controller.decoding = NO;
                NSData *pending = controller.pendingFrame;
                controller.pendingFrame = nil;
                if (pending) [controller receiveFrame:pending];
            });
        }
    });
}

- (CGSize)displayedImageSize {
    CGSize size = self.imageView.image.size;
    if (size.width < 1 || size.height < 1)
        size = CGSizeMake((CGFloat)PSDefaultStreamWidth, (CGFloat)PSDefaultStreamHeight);
    return size;
}

- (BOOL)normalizedPoint:(CGPoint)point clamp:(BOOL)clamp result:(CGPoint *)result {
    CGRect bounds = self.imageView.bounds;
    CGSize image = [self displayedImageSize];
    if (bounds.size.width < 1 || bounds.size.height < 1 || image.width < 1 || image.height < 1) return NO;
    CGFloat scale = MIN(bounds.size.width / image.width, bounds.size.height / image.height);
    CGSize shown = CGSizeMake(image.width * scale, image.height * scale);
    CGRect content = CGRectMake((bounds.size.width - shown.width) / 2,
                                (bounds.size.height - shown.height) / 2,
                                shown.width, shown.height);
    if (!clamp && !CGRectContainsPoint(content, point)) return NO;
    CGFloat nx = (point.x - content.origin.x) / content.size.width;
    CGFloat ny = (point.y - content.origin.y) / content.size.height;
    if (clamp) {
        nx = MAX(0, MIN(1, nx));
        ny = MAX(0, MIN(1, ny));
    } else if (nx < 0 || nx > 1 || ny < 0 || ny > 1) {
        return NO;
    }
    if (result) *result = CGPointMake(nx, ny);
    return YES;
}

- (BOOL)gestureRecognizer:(UIGestureRecognizer *)gestureRecognizer shouldReceiveTouch:(UITouch *)touch {
    return ![touch.view isKindOfClass:[UIControl class]];
}

- (void)sendInputEvent:(NSDictionary *)event {
    if (!event || !self.inputEnabled) return;
    if (self.cableMode) [self.cableServer sendEvent:event];
    else [self.input sendEvent:event];
}

- (void)sendPointer:(NSString *)type point:(CGPoint)point clamp:(BOOL)clamp {
    CGPoint normalized;
    CGPoint local = [self.view convertPoint:point toView:self.imageView];
    if (![self normalizedPoint:local clamp:clamp result:&normalized]) return;
    [self sendInputEvent:@{@"type": type, @"x": @(normalized.x), @"y": @(normalized.y)}];
}

- (void)showChrome {
    [self.chromeTimer invalidate];
    self.chromeTimer = nil;
    self.chromeHidden = NO;
    self.settingsButton.userInteractionEnabled = YES;
    self.keyboardButton.userInteractionEnabled = YES;
    [self setNeedsStatusBarAppearanceUpdate];
    [UIView animateWithDuration:0.22 animations:^{
        self.settingsButton.alpha = 1.0;
        self.keyboardButton.alpha = 1.0;
    }];
    [self scheduleChromeHide];
}

- (void)scheduleChromeHide {
    [self.chromeTimer invalidate];
    self.chromeTimer = nil;
    if (self.chromeHidden || !self.viewVisible) return;
    self.chromeTimer = [NSTimer scheduledTimerWithTimeInterval:PSChromeIdleDelay
                                                        target:self
                                                      selector:@selector(chromeTimerFired:)
                                                      userInfo:nil
                                                       repeats:NO];
}

- (void)chromeTimerFired:(NSTimer *)timer {
    self.chromeTimer = nil;
    [self hideChrome];
}

- (void)hideChrome {
    if (self.chromeHidden) return;
    self.chromeHidden = YES;
    self.settingsButton.userInteractionEnabled = NO;
    self.keyboardButton.userInteractionEnabled = NO;
    [self setNeedsStatusBarAppearanceUpdate];
    [UIView animateWithDuration:0.30 animations:^{
        self.settingsButton.alpha = 0.0;
        self.keyboardButton.alpha = 0.0;
    }];
}

- (BOOL)pointIsInChromeHotZone:(CGPoint)point {
    if (!self.chromeHidden) return NO;
    return point.x >= self.view.bounds.size.width - PSChromeHotZoneWidth &&
           point.y <= PSChromeHotZoneHeight;
}

- (void)handleTap:(UITapGestureRecognizer *)gesture {
    CGPoint point = [gesture locationInView:self.view];
    if ([self pointIsInChromeHotZone:point]) {
        // The Settings/keyboard controls faded out; a tap in the top-right
        // corner brings them back without sending a click to the PC.
        [self showChrome];
        return;
    }
    [self scheduleChromeHide];
    [self sendPointer:@"tap" point:point clamp:NO];
}

- (void)handleRightTap:(UITapGestureRecognizer *)gesture {
    [self scheduleChromeHide];
    [self sendPointer:@"rclick" point:[gesture locationInView:self.view] clamp:NO];
}

- (void)handleDrag:(UIPanGestureRecognizer *)gesture {
    if (gesture.state == UIGestureRecognizerStateBegan) {
        [self scheduleChromeHide];
        CGPoint normalized;
        CGPoint local = [self.view convertPoint:[gesture locationInView:self.view] toView:self.imageView];
        if (![self normalizedPoint:local clamp:NO result:&normalized]) {
            self.dragging = NO;
            return;
        }
        self.dragging = YES;
        self.lastMoveSent = 0;
        [self sendInputEvent:@{@"type": @"down", @"x": @(normalized.x), @"y": @(normalized.y)}];
    } else if (gesture.state == UIGestureRecognizerStateChanged && self.dragging) {
        NSTimeInterval now = [NSDate timeIntervalSinceReferenceDate];
        if (now - self.lastMoveSent < 0.035) return;
        self.lastMoveSent = now;
        [self sendPointer:@"move" point:[gesture locationInView:self.view] clamp:YES];
    } else if ((gesture.state == UIGestureRecognizerStateEnded ||
                gesture.state == UIGestureRecognizerStateCancelled ||
                gesture.state == UIGestureRecognizerStateFailed) && self.dragging) {
        self.dragging = NO;
        [self sendPointer:@"up" point:[gesture locationInView:self.view] clamp:YES];
    }
}

- (void)handleScroll:(UIPanGestureRecognizer *)gesture {
    if (gesture.state == UIGestureRecognizerStateBegan) {
        [self scheduleChromeHide];
        [gesture setTranslation:CGPointZero inView:self.view];
        return;
    }
    if (gesture.state != UIGestureRecognizerStateChanged) return;
    CGPoint translation = [gesture translationInView:self.view];
    if (fabs(translation.y) < 1.0) return;
    NSInteger dy = (NSInteger)lround(translation.y * 6.0);
    dy = MAX(-2400, MIN(2400, dy));
    [gesture setTranslation:CGPointZero inView:self.view];
    [self sendInputEvent:@{@"type": @"scroll", @"dy": @(dy)}];
}

- (void)openSettings {
    self.suspendForSettings = YES;
    [self stopEverything];
    self.navigationController.navigationBarHidden = NO;
    [self.navigationController pushViewController:[PSSettingsViewController new] animated:YES];
}

- (void)openKeyboard {
    if (!self.cableMode && (!self.token.length || ![self.token isKindOfClass:[NSString class]])) {
        [self setStatus:@"Pair with the PC host before opening the keyboard."];
        return;
    }
    PSKeyboardViewController *keyboard = [PSKeyboardViewController new];
    keyboard.eventSender = ^(NSDictionary *event){
        [self sendInputEvent:event];
    };
    UINavigationController *navigation = [[UINavigationController alloc] initWithRootViewController:keyboard];
    navigation.navigationBar.barStyle = UIBarStyleBlack;
    navigation.navigationBar.barTintColor = [UIColor colorWithWhite:0.08 alpha:1];
    navigation.navigationBar.tintColor = [UIColor colorWithRed:0.40 green:0.86 blue:0.82 alpha:1];
    navigation.navigationBar.translucent = NO;
    navigation.modalPresentationStyle = UIModalPresentationFormSheet;
    [self presentViewController:navigation animated:YES completion:nil];
}

- (void)appResignedActive {
    if (self.cableMode) return;
    [self stopEverything];
}

- (void)appBackgrounded {
    self.viewVisible = NO;
    [self stopEverything];
    [self stopFPSTimer];
}

- (void)appActivated {
    if (self.navigationController.topViewController != self) return;
    if (!self.view.window) return;
    self.viewVisible = YES;
    [self startFPSTimer];
    [self activateIfNeeded];
}

- (void)didReceiveMemoryWarning {
    [super didReceiveMemoryWarning];
    self.pendingFrame = nil;
}
@end
