package com.qaforge.tests;

import java.net.MalformedURLException;
import java.net.URL;
import java.time.Duration;
import org.openqa.selenium.By;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;
import org.testng.Assert;
import org.testng.annotations.AfterClass;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;
import io.appium.java_client.AppiumBy;
import io.appium.java_client.windows.WindowsDriver;
import io.appium.java_client.windows.options.WindowsOptions;

public class CalculatorTestById {
    private WindowsDriver driver;
    private CalculatorPageById calculatorPage;

    class CalculatorPageById {
        private WebElement num8Button;
        private WebElement multiplyButton;
        private WebElement num9Button;
        private WebElement plusButton;
        private WebElement num1Button;
        private WebElement num2Button;
        private WebElement equalButton;
        private WebElement divideButton;
        private WebElement num6Button;
        private WebElement num7Button;
        private WebElement xpower2Button;
        private WebElement squareRootButton;

        public void clickNum8Button() {
            num8Button = new WebDriverWait(driver, Duration.ofSeconds(15))
                    .until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("num8Button")));
            num8Button.click();
        }

        public void clickMultiplyButton() {
            multiplyButton = new WebDriverWait(driver, Duration.ofSeconds(15))
                    .until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("multiplyButton")));
            multiplyButton.click();
        }

        public void clickNum9Button() {
            num9Button = new WebDriverWait(driver, Duration.ofSeconds(15))
                    .until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("num9Button")));
            num9Button.click();
        }

        public void clickPlusButton() {
            plusButton = new WebDriverWait(driver, Duration.ofSeconds(15))
                    .until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("plusButton")));
            plusButton.click();
        }

        public void clickNum1Button() {
            num1Button = new WebDriverWait(driver, Duration.ofSeconds(15))
                    .until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("num1Button")));
            num1Button.click();
        }

        public void clickNum2Button() {
            num2Button = new WebDriverWait(driver, Duration.ofSeconds(15))
                    .until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("num2Button")));
            num2Button.click();
        }

        public void clickEqualButton() {
            equalButton = new WebDriverWait(driver, Duration.ofSeconds(15))
                    .until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("equalButton")));
            equalButton.click();
        }

        public void clickDivideButton() {
            divideButton = new WebDriverWait(driver, Duration.ofSeconds(15))
                    .until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("divideButton")));
            divideButton.click();
        }

        public void clickNum6Button() {
            num6Button = new WebDriverWait(driver, Duration.ofSeconds(15))
                    .until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("num6Button")));
            num6Button.click();
        }

        public void clickNum7Button() {
            num7Button = new WebDriverWait(driver, Duration.ofSeconds(15))
                    .until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("num7Button")));
            num7Button.click();
        }

        public void clickXpower2Button() {
            xpower2Button = new WebDriverWait(driver, Duration.ofSeconds(15))
                    .until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("xpower2Button")));
            xpower2Button.click();
        }

        public void clickSquareRootButton() {
            squareRootButton = new WebDriverWait(driver, Duration.ofSeconds(15))
                    .until(ExpectedConditions.elementToBeClickable(AppiumBy.accessibilityId("squareRootButton")));
            squareRootButton.click();
        }
    }

    @BeforeClass
    public void setUp() throws Exception {
        new ProcessBuilder("C:\\Windows\\System32\\calc.exe").start();
        WindowsOptions desktopOpts = new WindowsOptions();
        desktopOpts.setApp("Root");
        WindowsDriver desktopDriver = new WindowsDriver(new URL("http://127.0.0.1:4723"), desktopOpts);
        WebDriverWait desktopWait = new WebDriverWait(desktopDriver, Duration.ofSeconds(15));
        WebElement appWindow = desktopWait.until(
                ExpectedConditions.presenceOfElementLocated(
                        By.xpath("//Window[contains(@Name,'계산기')]")));
        String hexHandle = "0x" + Long.toHexString(Long.parseLong(appWindow.getAttribute("NativeWindowHandle")));
        desktopDriver.quit();

        WindowsOptions options = new WindowsOptions();
        options.setCapability("appTopLevelWindow", hexHandle);
        driver = new WindowsDriver(new URL("http://127.0.0.1:4723"), options);
        calculatorPage = new CalculatorPageById();
    }

    @Test
    public void testCalculator() {
        System.out.println("[STEP 1] Click num8Button");
        calculatorPage.clickNum8Button();
        System.out.println("[STEP 2] Click multiplyButton");
        calculatorPage.clickMultiplyButton();
        System.out.println("[STEP 3] Click num9Button");
        calculatorPage.clickNum9Button();
        System.out.println("[STEP 4] Click plusButton");
        calculatorPage.clickPlusButton();
        System.out.println("[STEP 5] Click num1Button");
        calculatorPage.clickNum1Button();
        System.out.println("[STEP 6] Click num2Button");
        calculatorPage.clickNum2Button();
        System.out.println("[STEP 7] Click equalButton");
        calculatorPage.clickEqualButton();
        System.out.println("[STEP 8] Click divideButton");
        calculatorPage.clickDivideButton();
        System.out.println("[STEP 9] Click num6Button");
        calculatorPage.clickNum6Button();
        System.out.println("[STEP 10] Click equalButton");
        calculatorPage.clickEqualButton();
        System.out.println("[STEP 11] Click num8Button");
        calculatorPage.clickNum8Button();
        System.out.println("[STEP 12] Click num9Button");
        calculatorPage.clickNum9Button();
        System.out.println("[STEP 13] Click plusButton");
        calculatorPage.clickPlusButton();
        System.out.println("[STEP 14] Click num8Button");
        calculatorPage.clickNum8Button();
        System.out.println("[STEP 15] Click num7Button");
        calculatorPage.clickNum7Button();
        System.out.println("[STEP 16] Click equalButton");
        calculatorPage.clickEqualButton();
        System.out.println("[STEP 17] Click xpower2Button");
        calculatorPage.clickXpower2Button();
        System.out.println("[STEP 18] Click multiplyButton");
        calculatorPage.clickMultiplyButton();
        System.out.println("[STEP 19] Click num9Button");
        calculatorPage.clickNum9Button();
        System.out.println("[STEP 20] Click squareRootButton");
        calculatorPage.clickSquareRootButton();
        System.out.println("[STEP 21] Click equalButton");
        calculatorPage.clickEqualButton();
        System.out.println("[STEP 22] Click squareRootButton");
        calculatorPage.clickSquareRootButton();
        System.out.println("[STEP 23] Click equalButton");
        calculatorPage.clickEqualButton();
        Assert.assertTrue(calculatorPage.equalButton.isDisplayed());
    }

    @AfterClass
    public void tearDown() {
        if (driver != null) {
            driver.quit();
        }
    }
}