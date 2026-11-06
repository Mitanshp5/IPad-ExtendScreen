#import "PSQRScannerViewController.h"
#import <AVFoundation/AVFoundation.h>

@interface PSQRScannerViewController () <AVCaptureMetadataOutputObjectsDelegate>
@property (nonatomic, strong) AVCaptureSession *session;
@property (nonatomic, strong) AVCaptureVideoPreviewLayer *previewLayer;
@property (nonatomic, strong) dispatch_queue_t captureQueue;
@property (nonatomic, strong) UIView *overlayView;
@property (nonatomic, strong) UIView *boxView;
@property (nonatomic, strong) UILabel *hintLabel;
@property (nonatomic) BOOL scanned;
@end

@implementation PSQRScannerViewController

- (void)viewDidLoad {
    [super viewDidLoad];
    self.title = @"Scan PC QR Code";
    self.view.backgroundColor = [UIColor blackColor];

    self.navigationItem.leftBarButtonItem = [[UIBarButtonItem alloc]
        initWithTitle:@"Cancel" style:UIBarButtonItemStylePlain
        target:self action:@selector(cancelScanning)];

    AVAuthorizationStatus auth = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeVideo];
    if (auth == AVAuthorizationStatusDenied || auth == AVAuthorizationStatusRestricted) {
        [self showCameraPermissionDenied];
        return;
    }

    [self setupCameraSession];
    [self setupOverlayUI];
}

- (void)setupCameraSession {
    self.session = [AVCaptureSession new];
    if ([self.session canSetSessionPreset:AVCaptureSessionPresetMedium]) {
        self.session.sessionPreset = AVCaptureSessionPresetMedium;
    }

    AVCaptureDevice *device = [AVCaptureDevice defaultDeviceWithMediaType:AVMediaTypeVideo];
    if (!device) {
        [self showNoCameraError];
        return;
    }

    if ([device isFocusModeSupported:AVCaptureFocusModeContinuousAutoFocus]) {
        NSError *lockError = nil;
        if ([device lockForConfiguration:&lockError]) {
            device.focusMode = AVCaptureFocusModeContinuousAutoFocus;
            [device unlockForConfiguration];
        }
    }

    NSError *inputError = nil;
    AVCaptureDeviceInput *input = [AVCaptureDeviceInput deviceInputWithDevice:device error:&inputError];
    if (!input || inputError || ![self.session canAddInput:input]) {
        [self showNoCameraError];
        return;
    }
    [self.session addInput:input];

    AVCaptureMetadataOutput *output = [AVCaptureMetadataOutput new];
    if (![self.session canAddOutput:output]) {
        [self showNoCameraError];
        return;
    }
    [self.session addOutput:output];

    self.captureQueue = dispatch_queue_create("extendscreen.qr.queue", DISPATCH_QUEUE_SERIAL);
    [output setMetadataObjectsDelegate:self queue:self.captureQueue];

    if ([output.availableMetadataObjectTypes containsObject:AVMetadataObjectTypeQRCode]) {
        [output setMetadataObjectTypes:@[AVMetadataObjectTypeQRCode]];
    }

    self.previewLayer = [AVCaptureVideoPreviewLayer layerWithSession:self.session];
    self.previewLayer.videoGravity = AVLayerVideoGravityResizeAspectFill;
    self.previewLayer.frame = self.view.bounds;
    [self.view.layer insertSublayer:self.previewLayer atIndex:0];
}

- (void)setupOverlayUI {
    self.overlayView = [UIView new];
    self.overlayView.backgroundColor = [UIColor clearColor];
    self.overlayView.userInteractionEnabled = NO;
    [self.view addSubview:self.overlayView];

    self.boxView = [UIView new];
    self.boxView.backgroundColor = [UIColor clearColor];
    self.boxView.layer.borderColor = [UIColor colorWithRed:0.22 green:0.74 blue:0.97 alpha:1.0].CGColor;
    self.boxView.layer.borderWidth = 3.0;
    self.boxView.layer.cornerRadius = 14.0;
    self.boxView.clipsToBounds = YES;
    [self.overlayView addSubview:self.boxView];

    self.hintLabel = [UILabel new];
    self.hintLabel.text = @"Point camera at the QR code on your PC screen";
    self.hintLabel.font = [UIFont boldSystemFontOfSize:17];
    self.hintLabel.textColor = [UIColor whiteColor];
    self.hintLabel.textAlignment = NSTextAlignmentCenter;
    self.hintLabel.numberOfLines = 0;
    self.hintLabel.layer.shadowColor = [UIColor blackColor].CGColor;
    self.hintLabel.layer.shadowOffset = CGSizeMake(0, 1);
    self.hintLabel.layer.shadowOpacity = 0.8;
    self.hintLabel.layer.shadowRadius = 2.0;
    [self.overlayView addSubview:self.hintLabel];
}

- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    if (self.previewLayer) {
        self.previewLayer.frame = self.view.bounds;
        UIInterfaceOrientation orientation = [UIApplication sharedApplication].statusBarOrientation;
        AVCaptureVideoOrientation videoOrientation = AVCaptureVideoOrientationPortrait;
        switch (orientation) {
            case UIInterfaceOrientationLandscapeLeft:
                videoOrientation = AVCaptureVideoOrientationLandscapeLeft;
                break;
            case UIInterfaceOrientationLandscapeRight:
                videoOrientation = AVCaptureVideoOrientationLandscapeRight;
                break;
            case UIInterfaceOrientationPortraitUpsideDown:
                videoOrientation = AVCaptureVideoOrientationPortraitUpsideDown;
                break;
            default:
                videoOrientation = AVCaptureVideoOrientationPortrait;
                break;
        }
        if (self.previewLayer.connection.isVideoOrientationSupported) {
            self.previewLayer.connection.videoOrientation = videoOrientation;
        }
    }

    self.overlayView.frame = self.view.bounds;
    CGFloat boxSide = MIN(self.view.bounds.size.width, self.view.bounds.size.height) * 0.58;
    boxSide = MAX(220, MIN(340, boxSide));
    CGFloat boxX = (self.view.bounds.size.width - boxSide) / 2.0;
    CGFloat boxY = (self.view.bounds.size.height - boxSide) / 2.0 - 20;
    self.boxView.frame = CGRectMake(boxX, boxY, boxSide, boxSide);
    self.hintLabel.frame = CGRectMake(20, boxY + boxSide + 24, self.view.bounds.size.width - 40, 50);
}

- (void)viewWillAppear:(BOOL)animated {
    [super viewWillAppear:animated];
    self.scanned = NO;
    if (self.session && !self.session.isRunning) {
        [self.session startRunning];
    }
}

- (void)viewWillDisappear:(BOOL)animated {
    [super viewWillDisappear:animated];
    if (self.session && self.session.isRunning) {
        [self.session stopRunning];
    }
}

- (void)cancelScanning {
    if (self.session && self.session.isRunning) {
        [self.session stopRunning];
    }
    [self dismissViewControllerAnimated:YES completion:nil];
}

- (void)showCameraPermissionDenied {
    UILabel *lbl = [[UILabel alloc] initWithFrame:CGRectInset(self.view.bounds, 40, 40)];
    lbl.text = @"Camera access is needed to scan the pairing QR code.\nPlease enable camera access in iPad Settings.";
    lbl.textColor = [UIColor whiteColor];
    lbl.textAlignment = NSTextAlignmentCenter;
    lbl.numberOfLines = 0;
    lbl.font = [UIFont systemFontOfSize:18];
    [self.view addSubview:lbl];
}

- (void)showNoCameraError {
    UILabel *lbl = [[UILabel alloc] initWithFrame:CGRectInset(self.view.bounds, 40, 40)];
    lbl.text = @"No camera is available on this device.";
    lbl.textColor = [UIColor whiteColor];
    lbl.textAlignment = NSTextAlignmentCenter;
    lbl.numberOfLines = 0;
    lbl.font = [UIFont systemFontOfSize:18];
    [self.view addSubview:lbl];
}

- (void)captureOutput:(AVCaptureOutput *)captureOutput
didOutputMetadataObjects:(NSArray *)metadataObjects
       fromConnection:(AVCaptureConnection *)connection {
    if (self.scanned) return;

    for (AVMetadataObject *obj in metadataObjects) {
        if (![obj isKindOfClass:[AVMetadataMachineReadableCodeObject class]]) continue;
        AVMetadataMachineReadableCodeObject *codeObj = (AVMetadataMachineReadableCodeObject *)obj;
        if (![codeObj.type isEqualToString:AVMetadataObjectTypeQRCode]) continue;

        NSString *rawString = codeObj.stringValue;
        if (!rawString.length) continue;

        NSString *hostURL = nil;
        NSString *pairCode = nil;

        if ([self parseQRData:rawString outHost:&hostURL outCode:&pairCode]) {
            self.scanned = YES;
            [self.session stopRunning];

            dispatch_async(dispatch_get_main_queue(), ^{
                [self handleScanSuccess:hostURL code:pairCode];
            });
            break;
        }
    }
}

- (BOOL)parseQRData:(NSString *)raw outHost:(NSString **)outHost outCode:(NSString **)outCode {
    if (!raw.length) return NO;
    NSString *trimmed = [raw stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];

    // 1. JSON format: {"host": "http://192.168.0.108:8644", "code": "12345678"}
    if ([trimmed hasPrefix:@"{"] && [trimmed hasSuffix:@"}"]) {
        NSData *data = [trimmed dataUsingEncoding:NSUTF8StringEncoding];
        if (data) {
            NSDictionary *dict = [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL];
            if ([dict isKindOfClass:[NSDictionary class]]) {
                NSString *h = dict[@"host"];
                NSString *c = dict[@"code"];
                if (h.length && c.length) {
                    if (outHost) *outHost = h;
                    if (outCode) *outCode = c;
                    return YES;
                }
            }
        }
    }

    // 2. Custom URI or HTTP URL with query parameter:
    //    http://192.168.0.108:8644/?code=12345678
    //    extendscreen://pair?host=http://192.168.0.108:8644&code=12345678
    NSURLComponents *comp = [NSURLComponents componentsWithString:trimmed];
    if (comp) {
        NSString *h = nil;
        NSString *c = nil;
        for (NSURLQueryItem *item in comp.queryItems) {
            if ([item.name.lowercaseString isEqualToString:@"host"]) {
                h = item.value;
            } else if ([item.name.lowercaseString isEqualToString:@"code"]) {
                c = item.value;
            }
        }

        NSString *scheme = comp.scheme.lowercaseString;
        if ([scheme isEqualToString:@"http"] || [scheme isEqualToString:@"https"]) {
            if (!h && comp.host.length) {
                if (comp.port) {
                    h = [NSString stringWithFormat:@"%@://%@:%@", scheme, comp.host, comp.port];
                } else {
                    h = [NSString stringWithFormat:@"%@://%@", scheme, comp.host];
                }
            }
        }

        if (h.length && c.length) {
            if (![h hasPrefix:@"http://"] && ![h hasPrefix:@"https://"]) {
                h = [NSString stringWithFormat:@"http://%@", h];
            }
            if (outHost) *outHost = h;
            if (outCode) *outCode = c;
            return YES;
        }
    }

    // 3. Fallback: space or newline separated "http://192.168.0.108:8644 12345678"
    NSArray *tokens = [trimmed componentsSeparatedByCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
    if (tokens.count >= 2) {
        NSString *part1 = tokens[0];
        NSString *part2 = tokens[1];
        if ([part1 hasPrefix:@"http"] && part2.length == 8) {
            if (outHost) *outHost = part1;
            if (outCode) *outCode = part2;
            return YES;
        }
    }

    return NO;
}

- (void)handleScanSuccess:(NSString *)hostURL code:(NSString *)pairCode {
    self.boxView.layer.borderColor = [UIColor colorWithRed:0.20 green:0.80 blue:0.40 alpha:1.0].CGColor;
    self.hintLabel.text = [NSString stringWithFormat:@"✓ Detected PC Host: %@", hostURL];
    self.hintLabel.textColor = [UIColor colorWithRed:0.20 green:0.80 blue:0.40 alpha:1.0];

    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.35 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        PSQRScanResultHandler handler = self.scanHandler;
        [self dismissViewControllerAnimated:YES completion:^{
            if (handler) {
                handler(hostURL, pairCode);
            }
        }];
    });
}

@end
