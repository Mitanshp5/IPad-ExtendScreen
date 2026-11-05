#import "PSKeyboardViewController.h"

@interface PSKeyboardViewController () <UITextFieldDelegate>
@property (nonatomic, strong) UIScrollView *scroll;
@property (nonatomic, strong) UILabel *intro;
@property (nonatomic, strong) UITextField *textField;
@property (nonatomic, strong) UIButton *sendButton;
@property (nonatomic, strong) NSArray *keyButtons;
@property (nonatomic, strong) NSArray *keyEvents;
@property (nonatomic) UIEdgeInsets savedContentInset;
@property (nonatomic) UIEdgeInsets savedIndicatorInset;
@property (nonatomic) BOOL keyboardInsetsSaved;
@end

@implementation PSKeyboardViewController
- (void)viewDidLoad {
    [super viewDidLoad];
    self.title = @"PC keyboard";
    self.view.backgroundColor = [UIColor colorWithRed:0.035 green:0.055 blue:0.075 alpha:1];
    self.navigationItem.leftBarButtonItem = [[UIBarButtonItem alloc]
        initWithTitle:@"Close" style:UIBarButtonItemStyleDone target:self action:@selector(close)];

    self.scroll = [[UIScrollView alloc] initWithFrame:self.view.bounds];
    self.scroll.autoresizingMask = UIViewAutoresizingFlexibleWidth | UIViewAutoresizingFlexibleHeight;
    self.scroll.keyboardDismissMode = UIScrollViewKeyboardDismissModeOnDrag;
    [self.view addSubview:self.scroll];

    self.intro = [UILabel new];
    self.intro.text = @"Text and keys go to the active window on the PC. Use the paired Wi-Fi or USB cable connection only.";
    self.intro.textColor = [UIColor colorWithWhite:0.74 alpha:1];
    self.intro.font = [UIFont systemFontOfSize:16];
    self.intro.numberOfLines = 0;
    [self.scroll addSubview:self.intro];

    self.textField = [UITextField new];
    self.textField.textColor = [UIColor whiteColor];
    self.textField.backgroundColor = [UIColor colorWithWhite:0.13 alpha:1];
    self.textField.borderStyle = UITextBorderStyleRoundedRect;
    self.textField.font = [UIFont systemFontOfSize:19];
    self.textField.autocapitalizationType = UITextAutocapitalizationTypeNone;
    self.textField.autocorrectionType = UITextAutocorrectionTypeNo;
    self.textField.spellCheckingType = UITextSpellCheckingTypeNo;
    self.textField.clearButtonMode = UITextFieldViewModeWhileEditing;
    self.textField.returnKeyType = UIReturnKeySend;
    self.textField.placeholder = @"Type text for the PC";
    self.textField.delegate = self;
    [self.scroll addSubview:self.textField];

    self.sendButton = [UIButton buttonWithType:UIButtonTypeSystem];
    [self.sendButton setTitle:@"Send text" forState:UIControlStateNormal];
    self.sendButton.titleLabel.font = [UIFont systemFontOfSize:20];
    [self.sendButton addTarget:self action:@selector(sendText) forControlEvents:UIControlEventTouchUpInside];
    [self.scroll addSubview:self.sendButton];

    NSArray *titles = @[@"Enter", @"Tab", @"Esc", @"Backspace",
                        @"Left", @"Up", @"Down", @"Right",
                        @"Home", @"End", @"Page Up", @"Page Down",
                        @"Ctrl+C", @"Ctrl+V", @"Ctrl+Z"];
    NSArray *events = @[
        @{@"type": @"key", @"name": @"enter"},
        @{@"type": @"key", @"name": @"tab"},
        @{@"type": @"key", @"name": @"esc"},
        @{@"type": @"key", @"name": @"backspace"},
        @{@"type": @"key", @"name": @"left"},
        @{@"type": @"key", @"name": @"up"},
        @{@"type": @"key", @"name": @"down"},
        @{@"type": @"key", @"name": @"right"},
        @{@"type": @"key", @"name": @"home"},
        @{@"type": @"key", @"name": @"end"},
        @{@"type": @"key", @"name": @"pageup"},
        @{@"type": @"key", @"name": @"pagedown"},
        @{@"type": @"key", @"name": @"c", @"mods": @[@"ctrl"]},
        @{@"type": @"key", @"name": @"v", @"mods": @[@"ctrl"]},
        @{@"type": @"key", @"name": @"z", @"mods": @[@"ctrl"]},
    ];
    self.keyEvents = events;
    NSMutableArray *buttons = [NSMutableArray array];
    for (NSUInteger index = 0; index < titles.count; index++) {
        UIButton *button = [UIButton buttonWithType:UIButtonTypeSystem];
        [button setTitle:titles[index] forState:UIControlStateNormal];
        [button setTitleColor:[UIColor whiteColor] forState:UIControlStateNormal];
        button.titleLabel.font = [UIFont systemFontOfSize:16];
        button.backgroundColor = [UIColor colorWithWhite:0.16 alpha:1];
        button.tag = (NSInteger)index;
        [button addTarget:self action:@selector(keyTapped:) forControlEvents:UIControlEventTouchUpInside];
        [self.scroll addSubview:button];
        [buttons addObject:button];
    }
    self.keyButtons = buttons;

    NSNotificationCenter *center = [NSNotificationCenter defaultCenter];
    [center addObserver:self selector:@selector(keyboardChanged:) name:UIKeyboardWillChangeFrameNotification object:nil];
    [center addObserver:self selector:@selector(keyboardHidden:) name:UIKeyboardWillHideNotification object:nil];
}

- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    CGFloat width = MIN(self.view.bounds.size.width - 48, 640);
    CGFloat left = (self.view.bounds.size.width - width) / 2;
    self.intro.frame = CGRectMake(left, 24, width, 54);
    self.textField.frame = CGRectMake(left, 92, width, 48);
    self.sendButton.frame = CGRectMake(left, 152, width, 44);
    CGFloat gap = 10;
    CGFloat columns = 4;
    CGFloat buttonWidth = floor((width - gap * (columns - 1)) / columns);
    CGFloat buttonHeight = 46;
    for (NSUInteger index = 0; index < self.keyButtons.count; index++) {
        NSUInteger row = index / (NSUInteger)columns;
        NSUInteger column = index % (NSUInteger)columns;
        UIButton *button = self.keyButtons[index];
        button.frame = CGRectMake(left + column * (buttonWidth + gap),
                                  220 + row * (buttonHeight + gap), buttonWidth, buttonHeight);
        button.layer.cornerRadius = 8;
    }
    self.scroll.contentSize = CGSizeMake(self.view.bounds.size.width,
        220 + ceil(self.keyButtons.count / columns) * (buttonHeight + gap) + 40);
}

- (void)sendEvent:(NSDictionary *)event {
    if (self.eventSender) self.eventSender(event);
}

- (void)sendText {
    NSString *text = self.textField.text;
    if (!text.length) return;
    [self sendEvent:@{@"type": @"text", @"text": text}];
    self.textField.text = @"";
}

- (void)keyTapped:(UIButton *)button {
    if (button.tag < 0 || (NSUInteger)button.tag >= self.keyEvents.count) return;
    [self sendEvent:self.keyEvents[(NSUInteger)button.tag]];
}

- (BOOL)textFieldShouldReturn:(UITextField *)textField {
    [self sendText];
    return NO;
}

- (void)close {
    [self.view endEditing:YES];
    [self dismissViewControllerAnimated:YES completion:nil];
}

- (void)restoreKeyboardInsets {
    if (!self.keyboardInsetsSaved) return;
    self.scroll.contentInset = self.savedContentInset;
    self.scroll.scrollIndicatorInsets = self.savedIndicatorInset;
    self.keyboardInsetsSaved = NO;
}

- (void)keyboardHidden:(NSNotification *)notification { [self restoreKeyboardInsets]; }

- (void)keyboardChanged:(NSNotification *)notification {
    if (!self.view.window) return;
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
    [self.scroll scrollRectToVisible:CGRectInset(self.textField.frame, 0, -80) animated:YES];
}

- (void)viewWillDisappear:(BOOL)animated {
    [self.view endEditing:YES];
    [self restoreKeyboardInsets];
    [super viewWillDisappear:animated];
}

- (void)dealloc {
    [[NSNotificationCenter defaultCenter] removeObserver:self];
}
@end
