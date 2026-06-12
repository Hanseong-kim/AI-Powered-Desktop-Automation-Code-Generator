package com.qaforge.tests;

import java.net.MalformedURLException;
import java.net.URL;
import java.time.Duration;

import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.interactions.Actions;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;
import org.testng.annotations.AfterClass;
import org.testng.annotations.BeforeClass;
import org.testng.annotations.Test;
import org.testng.Assert;

import io.appium.java_client.AppiumBy;
import io.appium.java_client.windows.WindowsDriver;
import io.appium.java_client.windows.options.WindowsOptions;

public class CalculatorTestByClass {
    private WindowsDriver driver;
    private CalculatorPageByClass calculatorPage;

    @BeforeClass
    public void setup() throws MalformedURLException {
        WindowsOptions options = new WindowsOptions();
        options.setApp("C:\\Windows\\System32\\calc.exe");
        driver = new WindowsDriver(new URL("http://127.0.0.1:4723"), options);
        calculatorPage = new CalculatorPageByClass(driver);
    }

    @Test
    public void testCalculator() {
        System.out.println("[STEP 1] Click on the 'Five' button");
        WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
        WebElement fiveButton = wait.until(ExpectedConditions.elementToBeClickable(By.className("Button")));
        Actions actions = new Actions(driver);
        actions.moveToElement(fiveButton).click().perform();

        System.out.println("[STEP 2] Type '5' into the 'Display' field");
        WebElement displayField = wait.until(ExpectedConditions.presenceOfElementLocated(By.name("Display")));
        displayField.clear();
        displayField.sendKeys("5");

        System.out.println("[STEP 3] Click on the 'Plus' button");
        WebElement plusButton = wait.until(ExpectedConditions.elementToBeClickable(By.name("Plus")));
        actions.moveToElement(plusButton).click().perform();

        System.out.println("[STEP 4] Click on the 'Three' button");
        WebElement threeButton = wait.until(ExpectedConditions.elementToBeClickable(By.name("Three")));
        actions.moveToElement(threeButton).click().perform();

        System.out.println("[STEP 5] Type '3' into the 'Display' field");
        displayField = wait.until(ExpectedConditions.presenceOfElementLocated(By.name("Display")));
        displayField.clear();
        displayField.sendKeys("3");

        System.out.println("[STEP 6] Click on the 'Equals' button");
        WebElement equalsButton = wait.until(ExpectedConditions.elementToBeClickable(By.name("Equals")));
        actions.moveToElement(equalsButton).click().perform();

        System.out.println("[STEP 7] Double click on the 'Result display' field");
        WebElement resultDisplay = wait.until(ExpectedConditions.presenceOfElementLocated(By.name("Display")));
        actions.moveToElement(resultDisplay).doubleClick().perform();

        System.out.println("[STEP 8] Scroll the window");
        actions.moveToElement(driver.findElement(By.className("ApplicationFrameWindow"))).scrollToElement(driver.findElement(By.className("ApplicationFrameWindow"))).perform();

        System.out.println("[STEP 9] Verify the result is displayed");
        WebElement result = wait.until(ExpectedConditions.presenceOfElementLocated(By.name("Display")));
        Assert.assertTrue(result.isDisplayed());
    }

    @AfterClass
    public void tearDown() {
        driver.quit();
    }

    class CalculatorPageByClass {
        private WindowsDriver driver;

        public CalculatorPageByClass(WindowsDriver driver) {
            this.driver = driver;
        }

        public void clickFiveButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement fiveButton = wait.until(ExpectedConditions.elementToBeClickable(By.className("Button")));
            Actions actions = new Actions(driver);
            actions.moveToElement(fiveButton).click().perform();
        }

        public void typeInDisplayField(String value) {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement displayField = wait.until(ExpectedConditions.presenceOfElementLocated(By.name("Display")));
            displayField.clear();
            displayField.sendKeys(value);
        }

        public void clickPlusButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement plusButton = wait.until(ExpectedConditions.elementToBeClickable(By.name("Plus")));
            Actions actions = new Actions(driver);
            actions.moveToElement(plusButton).click().perform();
        }

        public void clickThreeButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement threeButton = wait.until(ExpectedConditions.elementToBeClickable(By.name("Three")));
            Actions actions = new Actions(driver);
            actions.moveToElement(threeButton).click().perform();
        }

        public void clickEqualsButton() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement equalsButton = wait.until(ExpectedConditions.elementToBeClickable(By.name("Equals")));
            Actions actions = new Actions(driver);
            actions.moveToElement(equalsButton).click().perform();
        }

        public void doubleClickResultDisplay() {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(15));
            WebElement resultDisplay = wait.until(ExpectedConditions.presenceOfElementLocated(By.name("Display")));
            Actions actions = new Actions(driver);
            actions.moveToElement(resultDisplay).doubleClick().perform();
        }

        public void scrollWindow() {
            Actions actions = new Actions(driver);
            actions.moveToElement(driver.findElement(By.className("ApplicationFrameWindow"))).scrollToElement(driver.findElement(By.className("ApplicationFrameWindow"))).perform();
        }
    }
}