package com.qaforge.tests;

import java.net.MalformedURLException;
import java.net.URL;
import java.time.Duration;
import org.openqa.selenium.By;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.interactions.Actions;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;
import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;
import io.appium.java_client.AppiumBy;
import io.appium.java_client.windows.WindowsDriver;
import io.appium.java_client.windows.options.WindowsOptions;

public class CalculatorTestByClass {
    private WindowsDriver driver;
    private CalculatorPageByClass calculatorPage;

    @BeforeClass
    public void setUp() throws Exception {
        // 1. Launch (works for Win32 and UWP; setApp(exePath) alone fails for UWP)
        new ProcessBuilder("C:\\Windows\\System32\\calc.exe").start();

        // 2. Root session — wait for the window, capture its native handle
        WindowsOptions desktopOpts = new WindowsOptions();
        desktopOpts.setApp("Root");
        WindowsDriver desktopDriver = new WindowsDriver(new URL("http://127.0.0.1:4723"), desktopOpts);
        WebDriverWait desktopWait = new WebDriverWait(desktopDriver, Duration.ofSeconds(15));
        WebElement appWindow = desktopWait.until(
                ExpectedConditions.presenceOfElementLocated(
                        By.xpath("//Window[contains(@Name,'계산기')]")));
        String hexHandle = "0x" + Long.toHexString(Long.parseLong(appWindow.getAttribute("NativeWindowHandle")));
        desktopDriver.quit();

        // 3. Attach to the running window via appTopLevelWindow
        WindowsOptions options = new WindowsOptions();
        options.setCapability("appTopLevelWindow", hexHandle);
        driver = new WindowsDriver(new URL("http://127.0.0.1:4723"), options);
        calculatorPage = new CalculatorPageByClass(driver);
    }

    @Test
    public void testCalculator() {
        System.out.println("[STEP 1] Click on Five button");
        calculatorPage.clickFiveButton();

        System.out.println("[STEP 2] Type 5 into Display");
        calculatorPage.typeIntoDisplay("5");

        System.out.println("[STEP 3] Click on Plus button");
        calculatorPage.clickPlusButton();

        System.out.println("[STEP 4] Click on Three button");
        calculatorPage.clickThreeButton();

        System.out.println("[STEP 5] Type 3 into Display");
        calculatorPage.typeIntoDisplay("3");

        System.out.println("[STEP 6] Click on Equals button");
        calculatorPage.clickEqualsButton();

        System.out.println("[STEP 7] Double click on Result display");
        calculatorPage.doubleClickOnResultDisplay();

        System.out.println("[STEP 8] Scroll on ApplicationFrameWindow");
        calculatorPage.scrollOnApplicationFrameWindow();

        System.out.println("[STEP 9] Right click on Result display");
        calculatorPage.rightClickOnResultDisplay();

        Assert.assertTrue(calculatorPage.isResultDisplayDisplayed());
    }

    @AfterClass
    public void tearDown() {
        driver.quit();
    }

    private class CalculatorPageByClass {
        private WindowsDriver driver;

        public CalculatorPageByClass(WindowsDriver driver) {
            this.driver = driver;
        }

        public void clickFiveButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement fiveButton = wait.until(ExpectedConditions.elementToBeClickable(By.xpath("//Button[@Name='5']")));
            fiveButton.click();
        }

        public void typeIntoDisplay(String value) {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement display = wait.until(ExpectedConditions.presenceOfElementLocated(AppiumBy.accessibilityId("CalculatorResults")));
            display.clear();
            display.sendKeys(value);
        }

        public void clickPlusButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement plusButton = wait.until(ExpectedConditions.elementToBeClickable(By.xpath("//Button[@Name='더하기']")));
            plusButton.click();
        }

        public void clickThreeButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement threeButton = wait.until(ExpectedConditions.elementToBeClickable(By.xpath("//Button[@Name='3']")));
            threeButton.click();
        }

        public void clickEqualsButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement equalsButton = wait.until(ExpectedConditions.elementToBeClickable(By.xpath("//Button[@Name='일치']")));
            equalsButton.click();
        }

        public void doubleClickOnResultDisplay() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement resultDisplay = wait.until(ExpectedConditions.presenceOfElementLocated(AppiumBy.accessibilityId("CalculatorResults")));
            Actions actions = new Actions(driver);
            actions.doubleClick(resultDisplay).perform();
        }

        public void scrollOnApplicationFrameWindow() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement applicationFrameWindow = wait.until(ExpectedConditions.presenceOfElementLocated(By.className("ApplicationFrameWindow")));
            Actions actions = new Actions(driver);
            actions.moveToElement(applicationFrameWindow).moveByOffset(0, -300).perform();
        }

        public void rightClickOnResultDisplay() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement resultDisplay = wait.until(ExpectedConditions.presenceOfElementLocated(AppiumBy.accessibilityId("CalculatorResults")));
            Actions actions = new Actions(driver);
            actions.contextClick(resultDisplay).perform();
        }

        public boolean isResultDisplayDisplayed() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            return wait.until(ExpectedConditions.presenceOfElementLocated(AppiumBy.accessibilityId("CalculatorResults"))).isDisplayed();
        }
    }
}