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

public class CalculatorTestByClass {
    private WindowsDriver driver;
    private CalculatorPageByClass calculatorPage;

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
        calculatorPage = new CalculatorPageByClass(driver);
    }

    @Test
    public void testCalculator() {
        System.out.println("[STEP 1] Click on 8 button");
        calculatorPage.click8Button();

        System.out.println("[STEP 2] Click on 곱 button");
        calculatorPage.clickMultiplyButton();

        System.out.println("[STEP 3] Click on 9 button");
        calculatorPage.click9Button();

        System.out.println("[STEP 4] Click on 더하기 button");
        calculatorPage.clickPlusButton();

        System.out.println("[STEP 5] Click on 1 button");
        calculatorPage.click1Button();

        System.out.println("[STEP 6] Click on 2 button");
        calculatorPage.click2Button();

        System.out.println("[STEP 7] Click on 일치 button");
        calculatorPage.clickEqualButton();

        System.out.println("[STEP 8] Click on 나누기 button");
        calculatorPage.clickDivideButton();

        System.out.println("[STEP 9] Click on 6 button");
        calculatorPage.click6Button();

        System.out.println("[STEP 10] Click on 일치 button");
        calculatorPage.clickEqualButton();

        System.out.println("[STEP 11] Click on 8 button");
        calculatorPage.click8Button();

        System.out.println("[STEP 12] Click on 9 button");
        calculatorPage.click9Button();

        System.out.println("[STEP 13] Click on 더하기 button");
        calculatorPage.clickPlusButton();

        System.out.println("[STEP 14] Click on 8 button");
        calculatorPage.click8Button();

        System.out.println("[STEP 15] Click on 7 button");
        calculatorPage.click7Button();

        System.out.println("[STEP 16] Click on 일치 button");
        calculatorPage.clickEqualButton();

        System.out.println("[STEP 17] Click on 제곱 button");
        calculatorPage.clickSquareButton();

        System.out.println("[STEP 18] Click on 곱 button");
        calculatorPage.clickMultiplyButton();

        System.out.println("[STEP 19] Click on 9 button");
        calculatorPage.click9Button();

        System.out.println("[STEP 20] Click on 제곱근 button");
        calculatorPage.clickSquareRootButton();

        System.out.println("[STEP 21] Click on 일치 button");
        calculatorPage.clickEqualButton();

        System.out.println("[STEP 22] Click on 제곱근 button");
        calculatorPage.clickSquareRootButton();

        System.out.println("[STEP 23] Click on 일치 button");
        calculatorPage.clickEqualButton();

        Assert.assertTrue(calculatorPage.getEqualButton().isDisplayed());
    }

    @AfterClass
    public void tearDown() {
        if (driver != null) {
            driver.quit();
        }
    }

    class CalculatorPageByClass {
        private WindowsDriver driver;

        public CalculatorPageByClass(WindowsDriver driver) {
            this.driver = driver;
        }

        public void click8Button() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement button = wait.until(ExpectedConditions.elementToBeClickable(By.xpath("//Button[@Name='8']")));
            button.click();
        }

        public void clickMultiplyButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement button = wait.until(ExpectedConditions.elementToBeClickable(By.xpath("//Button[@Name='곱']")));
            button.click();
        }

        public void click9Button() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement button = wait.until(ExpectedConditions.elementToBeClickable(By.xpath("//Button[@Name='9']")));
            button.click();
        }

        public void clickPlusButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement button = wait.until(ExpectedConditions.elementToBeClickable(By.xpath("//Button[@Name='더하기']")));
            button.click();
        }

        public void click1Button() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement button = wait.until(ExpectedConditions.elementToBeClickable(By.xpath("//Button[@Name='1']")));
            button.click();
        }

        public void click2Button() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement button = wait.until(ExpectedConditions.elementToBeClickable(By.xpath("//Button[@Name='2']")));
            button.click();
        }

        public void clickEqualButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement button = wait.until(ExpectedConditions.elementToBeClickable(By.xpath("//Button[@Name='일치']")));
            button.click();
        }

        public void clickDivideButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement button = wait.until(ExpectedConditions.elementToBeClickable(By.xpath("//Button[@Name='나누기']")));
            button.click();
        }

        public void click6Button() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement button = wait.until(ExpectedConditions.elementToBeClickable(By.xpath("//Button[@Name='6']")));
            button.click();
        }

        public void clickSquareButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement button = wait.until(ExpectedConditions.elementToBeClickable(By.xpath("//Button[@Name='제곱']")));
            button.click();
        }

        public void clickSquareRootButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement button = wait.until(ExpectedConditions.elementToBeClickable(By.xpath("//Button[@Name='제곱근']")));
            button.click();
        }

        public void click7Button() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement button = wait.until(ExpectedConditions.elementToBeClickable(By.xpath("//Button[@Name='7']")));
            button.click();
        }

        public WebElement getEqualButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            return wait.until(ExpectedConditions.presenceOfElementLocated(By.xpath("//Button[@Name='일치']")));
        }
    }
}